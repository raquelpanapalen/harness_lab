"""Measure solver wave 4 (harness/wave4.py, build ticket 13) against the development runs' expected answers.
Evaluation only (reads harvester/data; the 5 most recent runs are held out).

    uv run harvester/eval_wave4.py [--runs N] [--family exfiltration]

Per family: tasks, confident, confident-and-correct, confident-but-wrong (must stay ~0: a confident wrong answer skips
the loop), and correct overall. Containment and actions are compared on the state they would produce
({quarantine, rotate, block}); notifications on the expected message fields. Images (standing order, artifacts) are
transcribed with tests/check_facts.py's evaluation transcriber, Jev verdicts cached there too.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "harvester"))
sys.path.insert(0, str(ROOT / "tests"))

from arena import llm  # noqa: E402
from check_facts import Transcriber, transcripts  # noqa: E402
from check_index import build, dev_runs  # noqa: E402
from eval_intake import label, load_tasks  # noqa: E402
from harness import wave4 as W  # noqa: E402
from harness.facts import Judge, facts_for  # noqa: E402
from harness.normalise import answer as normalise  # noqa: E402

ALL = ["containment:quarantine_agent", "containment:rotate_token", "containment:block_domain", "send_message"]
FAMILIES = {"exfiltration", "canaries", "breach_notification", "containment", "rotation", "first_token",
            "token_status", "board_origin", "inbox", "attacker_domains", "partner_draft"}
SCRATCH = ROOT / "runs" / "eval_wave4"
PROGRESS = Path("/tmp/claude-1000/eval_wave4.progress")  # tail -f to follow a long eval


def progress(line: str) -> None:
    """One flushed status line, also appended to the progress file."""
    line = f"{time.strftime('%H:%M:%S')} {line}"
    print(line, flush=True)
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS, "a") as fh:
        fh.write(line + "\n")


def run_family(fam: str, f, request: str, view=None, tr=None):
    if fam == "attacker_domains":
        return W.attacker_domains(f, request, lambda p: tr(view, p))
    if fam == "partner_draft":
        return W.partner_draft(f, request, ["write"])
    if fam == "inbox":
        from harness import inbox
        p = next(p for p in view.paths() if p.startswith("/inbox/"))
        text = "\n".join(view.index.docs[view.files[p]].lines or [])
        return inbox.route(f, request, text, None, inbox.ALL)
    if fam in ("rotation", "breach_notification", "containment"):
        return getattr(W, fam)(f, request, ALL)
    return getattr(W, fam)(f, request)


def state_of(actions: list[dict]) -> dict:
    q, rot, blk = [], [], []
    for a in actions:
        for o in (a["args"].get("ops") or []) if a["tool"] == "containment" else []:
            if o["op"] == "quarantine_agent":
                q.append(o["agent"])
            elif o["op"] == "rotate_token":
                rot.append({"token": o["token"], "at": o["at"]})
            elif o["op"] == "block_domain":
                blk.append(o["domain"])
    return {"quarantine": sorted(q), "rotate": sorted(rot, key=lambda x: x["token"]), "block": sorted(blk)}


def correct(fam: str, res, exp, outcome: str, request: str) -> bool:
    if res.outcome != outcome:
        return False
    if outcome != "OK":
        return sorted(res.answer or []) == sorted(exp or []) if isinstance(exp, list) else res.answer == exp
    if fam == "inbox" and isinstance(exp, dict) and exp.get("action"):
        if not res.actions:
            return False
        a = res.actions[0]
        if exp["action"] == "send_message":
            body = a["args"].get("body", "")
            return a["tool"] == "send_message" and a["args"]["to"] == exp["to"] and all(
                str(m).lower() in body.lower() for m in exp.get("must_mention", []))
        st = state_of(res.actions)
        if exp["action"] == "block_domain":
            return st["block"] == sorted(exp.get("domains", []))
        if exp["action"] == "quarantine_agent":
            return st["quarantine"] == [exp.get("agent")]
        return False
    if fam == "partner_draft":
        if res.answer != exp or not res.actions:
            return False
        c = res.actions[0]["args"]["content"]
        return all(k in c for k in ("INC-", "records", "domains", "UTC")) and "@" not in c and "://" not in c
    if fam == "containment":
        st = state_of(res.actions)
        want = {k: (sorted(exp.get(k, []), key=lambda x: x["token"]) if k == "rotate" else sorted(exp.get(k, [])))
                for k in ("quarantine", "rotate", "block")}
        return st == want
    if fam == "breach_notification":
        if not res.actions:
            return False
        body = res.actions[0]["args"]["body"]
        return res.actions[0]["args"]["to"] == exp.get("to") and str(exp.get("records_affected")) in body \
            and str(exp.get("detected_at")) in body
    got = normalise(res.answer, "object" if isinstance(exp, dict) else "list" if isinstance(exp, list) else "unknown",
                    request)
    return got == exp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int)
    ap.add_argument("--family")
    a = ap.parse_args()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    tracker = llm.CostTracker()
    judge = Judge(decide=lambda st, q, **kw: llm.decide(st, q, tracker=tracker),
                  chat=lambda m, **kw: llm.chat(m, tracker=tracker, **kw), cache_file=SCRATCH / "judge.json")
    tr = Transcriber(SCRATCH / "transcripts.json", tracker)
    stats: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    wrong: dict[str, list] = collections.defaultdict(list)
    runs = dev_runs()[: a.runs] if a.runs else dev_runs()
    requests = {(r["run_id"], r["task_id"]): r["request"] for r in load_tasks(None)}  # resolved (ticket/inbox) text
    progress(f"eval_wave4 start: {len(runs)} runs, families {a.family or 'all'}")
    for i, rdir in enumerate(runs, 1):
        t0, before = time.time(), {k: collections.Counter(v) for k, v in stats.items()}
        progress(f"[{i}/{len(runs)}] {rdir.name}: start")
        idx = build(rdir)
        revealed = {t["task_id"]: t for t in json.loads((rdir / "revealed.json").read_text())["tasks"]}
        for tid, t in revealed.items():
            request = requests.get((rdir.name, tid)) or t.get("instruction") or ""
            fam = label(request)
            if fam not in FAMILIES or (a.family and fam not in a.family.split(",")):
                continue
            view = idx.view(tid)
            f = facts_for(view, judge, transcripts(view, judge, tr))
            try:
                res = run_family(fam, f, request, view, tr)
            except Exception as e:  # noqa: BLE001
                res = W.Result().fail(f"{type(e).__name__}: {e}")
            exp, outcome = t.get("expected_answer"), t.get("expected_outcome", "OK")
            ok = correct(fam, res, exp, outcome, request)
            s = stats[fam]
            s["tasks"] += 1
            s["correct"] += ok
            s["confident"] += res.confident
            s["confident_correct"] += res.confident and ok
            s["confident_wrong"] += res.confident and not ok
            if ok and not res.confident:
                stats[fam]["why:" + (res.problems[0][:70] if res.problems else "?")] += 1
            if res.confident and not ok:
                print(f"  CONFIDENT WRONG {fam} {rdir.name} {tid}: got {json.dumps(res.answer)[:160]} | "
                      f"exp {json.dumps(exp)[:160]} | {json.dumps(state_of(res.actions))[:200] if res.actions else ''}",
                      flush=True)
            if not ok and len(wrong[fam]) < 4:
                wrong[fam].append((rdir.name, tid, res.confident, json.dumps(res.answer)[:120], json.dumps(exp)[:120],
                                   res.problems[:2], json.dumps(state_of(res.actions))[:160] if res.actions else ""))
        n = sum(s["tasks"] - before.get(k, collections.Counter())["tasks"] for k, s in stats.items())
        ok = sum(s["correct"] - before.get(k, collections.Counter())["correct"] for k, s in stats.items())
        conf = sum(s["confident"] - before.get(k, collections.Counter())["confident"] for k, s in stats.items())
        wrong = sum(s["confident_wrong"] - before.get(k, collections.Counter())["confident_wrong"]
                    for k, s in stats.items())
        progress(f"[{i}/{len(runs)}] {rdir.name}: done in {time.time() - t0:.0f}s, {n} tasks, {conf} confident, "
                 f"correct {ok / max(1, n):.3f}, confident wrong {wrong}, spend ${tracker.total:.4f}")
        from harness.facts import world as _w  # the facts cache holds every run's index: free it per run
        _w._CACHE.clear()
        del idx
        import gc
        gc.collect()
    print("\n| family | tasks | correct | confident | conf. correct | conf. WRONG |\n|---|---|---|---|---|---|")
    for fam, s in sorted(stats.items()):
        print(f"| {fam} | {s['tasks']} | {s['correct']} | {s['confident']} | {s['confident_correct']} | "
              f"{s['confident_wrong']} |")
    for fam, s in sorted(stats.items()):
        for k, v in s.items():
            if k.startswith("why:"):
                print(f"  {fam} correct but not confident ({v}): {k[4:]}")
    for fam, ws in wrong.items():
        for w in ws:
            print(f"  {fam} miss: {w}")
    print(f"model spend: ${tracker.total:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
