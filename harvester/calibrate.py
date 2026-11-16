"""Calibrate the local grader against the real one. No model calls, no state-changing tools.

For each chosen harvested run: start a dev run with the same seed (same tasks and answers, new surface), match each
new task to its original by content, submit a deliberate VARIANT of the known answer (exact, one item missing, an
extra item, reordered, other timestamp format, extra/shifted citations, ...), finish, and store the real feedback
next to the run's revealed answers. `--report` then scores every recorded submission with the local grader and
shows where it disagrees with the real one.

    uv run harvester/calibrate.py --runs 4        # 4 calibration runs (seeds of harvested runs)
    uv run harvester/calibrate.py --report        # compare local grader vs real, per variant
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import re
import sys
import time
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from arena.client import ArenaClient, ArenaError  # noqa: E402
from replay.grade import ORDER_RX, answer_mode, compare, grade_sources, task_kind  # noqa: E402
from replay.tools import parse_ts  # noqa: E402

DATA = HERE / "data"
OUT = HERE / "data" / "calibration"
ANSWER_KINDS = {"answer", "refusal"}


def words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9:_@.-]{2,}", (s or "").lower())}


def ticket_text(c: ArenaClient, run: str, tid: str) -> str:
    """The open ticket's text, for tasks whose instruction only says 'work the open ticket'."""
    try:
        lst = c.call_tool(run, tid, "list", {"path": "/tickets"})
        names = [e["name"] for e in lst.get("result", {}).get("entries", []) if e["name"].endswith(".md")]
        for n in names:
            r = c.call_tool(run, tid, "read", {"path": f"/tickets/{n}"})
            text = r.get("result", {}).get("text", "") if r.get("ok") else ""
            if re.search(r"status:\s*open", text):
                return text
    except ArenaError:
        pass
    return ""


def match(new_tasks: list[dict], texts: dict[str, str], orig: dict[str, dict]) -> dict[str, str]:
    """Greedy one-to-one match of new task ids to original task ids: same level, most shared words."""
    pairs = []
    for t in new_tasks:
        wn = words(texts[t["task_id"]])
        for oid, o in orig.items():
            if o.get("level") != t.get("level"):
                continue
            wo = words(o.get("request") or o["instruction"])
            pairs.append((len(wn & wo) / (len(wn | wo) or 1), t["task_id"], oid))
    pairs.sort(reverse=True)
    out, used = {}, set()
    for score, nid, oid in pairs:
        if nid not in out and oid not in used:
            out[nid] = oid
            used.add(oid)
    return out


# --- variants ------------------------------------------------------------------------------------------
def shift_ts(s: str, secs: int = 0, plus00: bool = False) -> str:
    t = parse_ts(s)
    if not t:
        return s
    t = t + timedelta(seconds=secs)
    return t.strftime("%Y-%m-%dT%H:%M:%S") + ("+00:00" if plus00 else "Z")


def is_ts(x) -> bool:
    return isinstance(x, str) and bool(re.match(r"\d{4}-\d\d-\d\dT", x)) and parse_ts(x) is not None


def variants_for(ans, ordered: bool, outcome: str) -> list[str]:
    if outcome == "UNSUPPORTED":
        return ["exact", "answer_text_instead_of_null"]
    if outcome == "DENIED_SECURITY":
        return ["exact", "email_upper", "wrong_outcome_ok"]
    if isinstance(ans, list):
        if ordered:
            return ["exact", "swap_adjacent", "reversed"]
        base = ["exact", "drop1", "extra1", "extra1_drop1", "shuffled", "lowercase_items"]
        return base + (["drop_half", "extra_half"] if len(ans) >= 4 else [])
    if isinstance(ans, dict):
        v = ["exact", "dict_one_wrong", "dict_missing_key", "dict_values_as_strings"]
        if any(is_ts(x) for x in ans.values()):
            v += ["dict_ts_plus00", "dict_ts_off1s"]
        return v
    if is_ts(ans):
        return ["exact", "ts_plus00", "ts_off1s", "ts_off60s"]
    if isinstance(ans, (int, float)) or (isinstance(ans, str) and re.fullmatch(r"-?\d+", ans)):
        return ["exact", "num_as_other_type", "num_off1"]
    return ["exact", "case_changed", "wrong"]


def make_answer(ans, v: str, rng: random.Random):
    if v in ("exact", "wrong_outcome_ok") or v.startswith("src_"):
        return ans
    if v == "answer_text_instead_of_null":
        return "not supported"
    if v == "email_upper":
        return ans.upper()
    if v == "drop1":
        return ans[1:]
    if v == "drop_half":
        return ans[len(ans) // 2:]
    if v == "extra1":
        return ans + ["ZZ-9999"]
    if v == "extra_half":
        return ans + [f"ZZ-99{i:02d}" for i in range(max(1, len(ans) // 2))]
    if v == "extra1_drop1":
        return ans[1:] + ["ZZ-9999"]
    if v == "shuffled":
        x = list(ans)
        while len(x) > 1 and x == ans:
            rng.shuffle(x)
        return x
    if v == "lowercase_items":
        return [a.lower() if isinstance(a, str) else a for a in ans]
    if v == "swap_adjacent":
        x = list(ans)
        if len(x) > 1:
            x[0], x[1] = x[1], x[0]
        return x
    if v == "reversed":
        return list(reversed(ans))
    if v == "dict_one_wrong":
        k = next(iter(ans))
        return {**ans, k: "wrong-value" if not isinstance(ans[k], list) else ["wrong-value"]}
    if v == "dict_missing_key":
        k = list(ans)[-1]
        return {kk: vv for kk, vv in ans.items() if kk != k}
    if v == "dict_values_as_strings":
        return {k: (str(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else
                    (int(x) if isinstance(x, str) and re.fullmatch(r"\d+", x) else x)) for k, x in ans.items()}
    if v == "dict_ts_plus00":
        return {k: shift_ts(x, plus00=True) if is_ts(x) else x for k, x in ans.items()}
    if v == "dict_ts_off1s":
        return {k: shift_ts(x, 1) if is_ts(x) else x for k, x in ans.items()}
    if v == "ts_plus00":
        return shift_ts(ans, plus00=True)
    if v == "ts_off1s":
        return shift_ts(ans, 1)
    if v == "ts_off60s":
        return shift_ts(ans, 60)
    if v == "num_as_other_type":
        return str(ans) if isinstance(ans, (int, float)) else int(ans)
    if v == "num_off1":
        return (ans + 1) if isinstance(ans, (int, float)) else str(int(ans) + 1)
    if v == "case_changed":
        return ans.swapcase() if isinstance(ans, str) else ans
    if v == "wrong":
        return "definitely-wrong"
    return ans


SOURCE_VARIANTS = ["src_none", "src_extra3", "src_extra10", "src_line_plus2", "src_line_plus3", "src_half",
                   "src_dup"]


def make_sources(srcs: list[dict], v: str) -> list[dict]:
    srcs = [dict(s) for s in srcs or []]
    if v == "src_none":
        return []
    if v == "src_extra3":
        return srcs + [{"path": "/AGENTS.md", "line": i} for i in (5, 9, 13)]
    if v == "src_extra10":
        return srcs + [{"path": "/AGENTS.md", "line": i} for i in range(1, 30, 3)]
    if v in ("src_line_plus2", "src_line_plus3"):
        d = 2 if v.endswith("2") else 3
        return [{**s, "line": s["line"] + d} if "line" in s else s for s in srcs]
    if v == "src_half":
        return srcs[: max(1, len(srcs) // 2)]
    if v == "src_dup":
        return srcs + srcs
    return srcs


# --- one calibration run -----------------------------------------------------------------------------------
def calibrate(c: ArenaClient, src: str, rng: random.Random, counter: collections.Counter) -> None:
    rdir = DATA / "runs" / src
    run = json.loads((rdir / "run.json").read_text())
    orig = {t["task_id"]: t for t in json.loads((rdir / "revealed.json").read_text())["tasks"]}
    r = c.start_run("dev", seed=run["seed"], meta={"note": f"calibration: answer variants for seed of {src}"})
    R = r.run_id
    print(f"{src} seed {run['seed']} -> {R} ({len(r.tasks)} tasks)")
    records = []
    try:
        new = [t.model_dump() for t in r.tasks]
        texts = {}
        for t in new:
            txt = t["instruction"]
            if "/tickets/" in txt:
                txt += "\n" + ticket_text(c, R, t["task_id"])
            texts[t["task_id"]] = txt
        mapping = match(new, texts, orig)
        for t in new:
            nid = t["task_id"]
            o = orig.get(mapping.get(nid, ""))
            if not o:
                continue
            kind = task_kind(o["expected_outcome"], o["expected_answer"])
            if kind not in ANSWER_KINDS:
                continue
            ans, out = o["expected_answer"], o["expected_outcome"]
            ordered = bool(ORDER_RX.search(o.get("request") or o["instruction"]))
            cands = variants_for(ans, ordered, out)
            if out == "OK" and o.get("expected_sources"):
                cands = cands + SOURCE_VARIANTS
            # least-used variant first, so every variant collects data points
            v = min(cands, key=lambda x: (counter[x], rng.random()))
            counter[v] += 1
            sub_ans = make_answer(ans, v, rng)
            sub_out = "OK" if v == "wrong_outcome_ok" else out
            sub_src = make_sources(o.get("expected_sources"), v) if v.startswith("src_") else o.get("expected_sources")
            try:
                fb = c.submit(R, nid, sub_out, sub_ans, sub_src or [])
            except ArenaError as e:
                fb = {"error": str(e)}
            records.append({"task_id": nid, "orig_task_id": o["task_id"], "variant": v, "kind": kind,
                            "ordered": ordered, "submitted": {"outcome": sub_out, "answer": sub_ans,
                                                              "sources": sub_src or []}, "feedback": fb})
    finally:
        c.finish(R)
    revealed = {t["task_id"]: t for t in c.get_run(R)["tasks"]}
    for rec in records:
        t = revealed[rec["task_id"]]
        rec["truth"] = {k: t.get(k) for k in ("expected_outcome", "expected_answer", "expected_sources", "request",
                                              "instruction", "grade")}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{R}.json").write_text(json.dumps({"run_id": R, "seed_of": src, "records": records}, indent=2,
                                              ensure_ascii=False))
    ok = sum(1 for x in records if x["truth"]["expected_answer"] == orig[x["orig_task_id"]]["expected_answer"])
    print(f"  {len(records)} submissions; mapping correct for {ok}/{len(records)}")


# --- report ----------------------------------------------------------------------------------------------
def report() -> int:
    rows = []
    for f in sorted(OUT.glob("*.json")):
        for rec in json.loads(f.read_text())["records"]:
            fb, tr, sub = rec["feedback"], rec["truth"], rec["submitted"]
            if "score" not in fb:
                continue
            out_ok = sub["outcome"] == tr["expected_outcome"]
            kind = task_kind(tr["expected_outcome"], tr["expected_answer"])
            if not out_ok:
                a = 0.0
            elif tr["expected_outcome"] == "UNSUPPORTED":
                a = 1.0
            else:
                a = compare(sub["answer"], tr["expected_answer"],
                            mode=answer_mode(tr.get("request") or tr.get("instruction") or "", tr["expected_outcome"]))
            hits, n, s = grade_sources(sub["sources"], tr["expected_sources"])
            score = a * (0.7 + 0.3 * s) if a > 0 else 0.0
            rows.append({"variant": rec["variant"], "kind": kind, "real_a": fb.get("answer_score"), "mine_a": a,
                         "real_s": fb.get("sources_score"), "mine_s": s, "real_hits": fb.get("sources_hit"),
                         "mine_hits": hits, "real": fb["score"], "mine": score, "rec": rec})
    if not rows:
        print("no calibration data yet")
        return 1
    by = collections.defaultdict(list)
    for r in rows:
        by[r["variant"]].append(r)
    print(f"{len(rows)} graded submissions\n")
    print(f"{'variant':<28}{'n':>4}  {'real ans':>9} {'mine ans':>9}  {'real src':>9} {'mine src':>9}  {'agree':>6}")
    for v in sorted(by):
        rs = by[v]
        mean = lambda k: sum((x[k] or 0) for x in rs) / len(rs)
        agree = sum(abs(x["real"] - x["mine"]) < 0.011 for x in rs)
        print(f"{v:<28}{len(rs):>4}  {mean('real_a'):>9.3f} {mean('mine_a'):>9.3f}  {mean('real_s'):>9.3f} "
              f"{mean('mine_s'):>9.3f}  {agree:>3}/{len(rs)}")
    bad = [r for r in rows if abs(r["real"] - r["mine"]) >= 0.011]
    print(f"\n{len(bad)} disagreements (score differs by > 0.01); examples:")
    for r in bad[:25]:
        rec = r["rec"]
        print(f"  [{r['variant']}] real a={r['real_a']} s={r['real_s']} ({r['real_hits']}/{rec['feedback'].get('sources_expected')}) "
              f"score={r['real']} | mine a={r['mine_a']:.3f} s={r['mine_s']:.3f} ({r['mine_hits']}) score={r['mine']:.3f}")
        print(f"      expected {json.dumps(rec['truth']['expected_answer'], ensure_ascii=False)[:140]}")
        print(f"      submitted {json.dumps(rec['submitted']['answer'], ensure_ascii=False)[:140]}")
        print(f"      feedback {rec['feedback'].get('feedback')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--runs", type=int, default=4)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--sleep", type=float, default=3)
    a = ap.parse_args()
    if a.report:
        return report()
    c = ArenaClient()
    done = {json.loads(f.read_text())["seed_of"] for f in OUT.glob("*.json")} if OUT.exists() else set()
    srcs = [l and json.loads(l)["run_id"] for l in (DATA / "index.jsonl").read_text().splitlines()]
    srcs = [s for s in srcs if s not in done][: a.runs]
    rng = random.Random(7)
    counter: collections.Counter = collections.Counter()
    for i, src in enumerate(srcs):
        if i:
            time.sleep(a.sleep)
        calibrate(c, src, rng, counter)
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
