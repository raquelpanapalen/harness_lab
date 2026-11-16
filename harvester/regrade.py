"""Re-grade finished replay runs with the current grader, without new model calls.

    uv run harvester/regrade.py harvester/replay_results/baseline/rp00*.json

For each task: a fresh workspace of the harvested run, the task's state-changing calls replayed from the harness
trace (runs/<run_id>/<task>.jsonl), then the saved submission graded again. Prints old vs new accuracy per run.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from replay import tools as T  # noqa: E402
from replay.grade import AUTHORISED, grade, task_kind  # noqa: E402
from replay.server import Store  # noqa: E402

warnings.filterwarnings("ignore")


def trace_actions(run_id: str, tid: str) -> list[tuple[str, dict]]:
    f = ROOT / "runs" / run_id / f"{tid}.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.open():
        e = json.loads(line)
        if e.get("event") == "tool" and e.get("name") in T.STATE_CHANGING:
            out.append((e["name"], e.get("args") or {}))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+", type=Path)
    ap.add_argument("--write", action="store_true", help="store the new grades in the result files")
    a = ap.parse_args()
    store = Store(HERE / "data")
    for p in a.results:
        r = json.loads(p.read_text())
        src = r.get("replay_of") or r["run_id"].split("_", 1)[1]
        d = store.load(src)
        files = {t["task_id"]: t["files"] for t in d["manifest"]["tasks"]}
        old = new = 0.0
        changed = []
        for t in r["tasks"]:
            tid, truth = t["task_id"], d["revealed"][t["task_id"]]
            g_old = (t.get("grade") or {}).get("score", 0.0)
            old += g_old
            if not t.get("submission"):
                continue
            ws = T.Workspace(files[tid], store.blobs, store.page_counts, d["intel"], None)
            ws.authorized = AUTHORISED.get(task_kind(truth["expected_outcome"], truth["expected_answer"],
                                                     truth.get("request") or t.get("instruction", "")), set())
            for tool, args in trace_actions(r["run_id"], tid):
                T.call(ws, tool, args)
            g = grade({**truth, "task_id": tid}, t["submission"], ws)
            new += g["score"]
            if abs(g["score"] - g_old) > 0.011:
                changed.append((tid, g_old, g["score"], g["feedback"][2][:70]))
            if a.write:
                t["grade"] = g
        n = len(r["tasks"])
        print(f"{r['run_id']}: accuracy {old / n:.3f} -> {new / n:.3f}  ({len(changed)} tasks changed)")
        for c in changed:
            print(f"   {c[0]} {c[1]:.2f} -> {c[2]:.2f}  {c[3]}")
        if a.write:
            r["accuracy"] = round(new / n, 4)
            p.write_text(json.dumps(r, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
