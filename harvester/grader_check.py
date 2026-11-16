"""Check the local grader against every real grading we have, each in its own real workspace.

    uv run harvester/grader_check.py            # summary + disagreements
    uv run harvester/grader_check.py -v         # every disagreement with submission and expectation

Data (all git-ignored, under harvester/data/):
- calibration/<run>.json  deliberately varied submissions (calibrate.py) with the real feedback
- compare/<run>.json      real runs of the harness (GET /runs after finish: submissions + real grades)
- compare/runs/<run>/manifest.json + compare/blobs/   those runs' workspaces (harvest.Harvester.fetch_files)
Only tasks whose grade depends on the submission alone (answer / refusal) are re-graded; real gradings that failed a
constraint (other teams' injections, unauthorised actions) are counted separately: the replay checks those itself.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from replay import tools as T  # noqa: E402
from replay.grade import AUTHORISED, answer_mode, compare, grade, grade_sources, task_kind  # noqa: E402
from replay.tools import Workspace  # noqa: E402

DATA = HERE / "data"
CMP = DATA / "compare"


def workspace(run_id: str, task_id: str, _cache: dict = {}) -> Workspace | None:  # noqa: B006
    if run_id not in _cache:
        m = CMP / "runs" / run_id / "manifest.json"
        _cache[run_id] = {t["task_id"]: t["files"] for t in json.loads(m.read_text())["tasks"]} if m.exists() else {}
    files = _cache[run_id].get(task_id)
    return Workspace(files, CMP / "blobs", {}, None) if files else None


def local_grade(truth: dict, sub: dict, ws: Workspace | None) -> dict:
    out_ok = sub.get("outcome") == truth["expected_outcome"]
    if not out_ok:
        a = 0.0
    elif truth["expected_outcome"] == "UNSUPPORTED":
        a = 1.0
    else:
        a = compare(sub.get("answer"), truth["expected_answer"],
                    mode=answer_mode(truth.get("request") or truth.get("instruction") or "", truth["expected_outcome"]))
    hits, n, s = grade_sources(sub.get("sources") or [], truth.get("expected_sources") or [], ws)
    return {"answer_score": a, "sources_score": s, "sources_hit": hits, "score": a * (0.7 + 0.3 * s) if a > 0 else 0.0}


def gradings():
    """(origin, run_id, task_id, truth, submission, real feedback)"""
    for f in sorted((DATA / "calibration").glob("*.json")):
        d = json.loads(f.read_text())
        for r in d["records"]:
            if "score" in r["feedback"]:
                yield "calibration:" + r["variant"], d["run_id"], r["task_id"], r["truth"], r["submitted"], r["feedback"]
    for f in sorted(CMP.glob("run_*.json")):
        if f.name.endswith(".actions.json"):
            continue
        d = json.loads(f.read_text())
        for t in d["tasks"]:
            if t.get("submission") and t.get("grade"):
                yield "harness run", d["run_id"], t["task_id"], t, t["submission"], t["grade"]


def authorised_workspace(run: str, tid: str, truth: dict) -> Workspace | None:
    w = workspace(run, tid)
    if w is not None:
        w.authorized = AUTHORISED.get(task_kind(truth["expected_outcome"], truth["expected_answer"],
                                                truth.get("request") or truth.get("instruction", "")), set())
    return w


def check_full(wss: dict) -> tuple[Counter, Counter, list[str]]:
    """Complete local grade (constraints included) of every real grading, in a workspace that has the task's real
    state-changing calls applied (harness runs; calibration made none). Action tasks of calibration are skipped."""
    agree, total, bad = Counter(), Counter(), []
    for origin, run, tid, truth, sub, fb in gradings():
        group = origin.split(":")[0]
        kind = task_kind(truth["expected_outcome"], truth["expected_answer"], truth.get("request") or truth.get("instruction"))
        if group == "calibration" and kind not in ("answer", "refusal", "rotation"):
            continue
        ws = wss.get((run, tid)) or authorised_workspace(run, tid, truth)
        if ws is None:
            continue
        g = grade({**truth, "task_id": tid}, sub, ws)
        total[group] += 1
        if abs(g["score"] - fb["score"]) <= 0.011 and g["constraints_ok"] == fb.get("constraints_ok"):
            agree[group] += 1
        else:
            bad.append(f"{origin:<24} {run[-6:]} {tid} {kind:<20} real {fb['score']} {fb['feedback'][2][:60]}\n"
                       f"      local {g['score']} {g['feedback'][2][:90]} | {g['feedback'][1]}")
    return agree, total, bad


def check_actions(wss: dict) -> tuple[int, list[str]]:
    """Replay the real state-changing calls of harness runs (compare/<run>.actions.json) in the same workspaces,
    in the same order, and compare the responses with the real ones. Leaves the workspaces in `wss[(run, task)]`."""
    same, bad = 0, []
    for f in sorted(CMP.glob("run_*.actions.json")):
        rid = f.name.split(".")[0]
        real = {t["task_id"]: t for t in json.loads((CMP / f"{rid}.json").read_text())["tasks"]}
        for a in json.loads(f.read_text()):
            t = real[a["task"]]
            if (rid, a["task"]) not in wss:
                w = authorised_workspace(rid, a["task"], t)
                if w is None:
                    break
                wss[(rid, a["task"])] = w
            r = T.call(wss[(rid, a["task"])], a["tool"], a["args"])
            mine = json.dumps(r["result"] if r["ok"] else {"error": r["error"]}, ensure_ascii=False)
            if mine[:2000] == str(a["output"])[:2000]:  # traces keep the first 2000 characters
                same += 1
            else:
                bad.append(f"{rid[-6:]} {a['task']} {a['tool']}\n      real {str(a['output'])[:300]}\n      mine {mine[:300]}")
    return same, bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    agree, total, constraint_fail, no_ws = Counter(), Counter(), Counter(), 0
    bad = []
    for origin, run, tid, truth, sub, fb in gradings():
        group = origin.split(":")[0]
        if task_kind(truth["expected_outcome"], truth["expected_answer"]) not in ("answer", "refusal", "rotation"):
            continue
        if fb.get("constraints_ok") is False:
            constraint_fail[group] += 1
            continue
        ws = workspace(run, tid)
        no_ws += ws is None
        g = local_grade(truth, sub, ws)
        total[group] += 1
        if abs(g["score"] - fb["score"]) <= 0.011:
            agree[group] += 1
        else:
            bad.append((origin, run, tid, truth, sub, fb, g))
    for k in total:
        print(f"{k:<12} local = real on {agree[k]}/{total[k]}   (+{constraint_fail[k]} real constraint failures, "
              f"not re-graded)")
    if no_ws:
        print(f"({no_ws} without a downloaded workspace: citations matched by locator only)")
    wss: dict = {}
    same, abad = check_actions(wss)
    print(f"state-changing tools: {same}/{same + len(abad)} real responses identical")
    for x in abad:
        print("  DIFF", x)
    fagree, ftotal, fbad = check_full(wss)
    for k in ftotal:
        print(f"full grade   {k:<12} local = real (score and constraint verdict) on {fagree[k]}/{ftotal[k]}")
    for x in fbad:
        print("  FULL", x)
    print(f"\n{len(bad)} disagreements:")
    for origin, run, tid, truth, sub, fb, g in bad:
        print(f"  {origin:<28} {run[-6:]} {tid}  real {fb['score']} (a {fb.get('answer_score')}, s "
              f"{fb.get('sources_score')} {fb.get('sources_hit')}/{fb.get('sources_expected')})  local "
              f"{g['score']:.3f} (a {g['answer_score']:.3f}, s {g['sources_score']:.3f} {g['sources_hit']})")
        if a.v:
            print(f"      exp {truth['expected_outcome']} {json.dumps(truth['expected_answer'])[:200]}")
            print(f"      got {sub.get('outcome')} {json.dumps(sub.get('answer'))[:200]}")
            print(f"      exp src {json.dumps(truth.get('expected_sources'))[:300]}")
            print(f"      got src {json.dumps(sub.get('sources'))[:300]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
