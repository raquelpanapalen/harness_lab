"""Score family solvers offline on harvested development runs (build ticket 10+): each solver runs on its family's
tasks with a stand-in ctx (real Jev / Gemma through the same pacer interface), and the replay grader scores the
draft (answer, outcome, citations). Evaluation only: reads harvester/data. Held-out runs skipped.

    uv run tests/check_solvers.py [--runs N] [--family F] [-v]

Families are recognised here by request patterns (the harness uses intake's Jev family). Per family: tasks, how
often the solver was confident, mean Score of confident drafts, and the mean Score the family would get if every
unconfident task scored 0 (the solver's own contribution; the loop takes those tasks in the harness).
"""
from __future__ import annotations

import collections
import json
import re
import sys
import threading
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "harvester"))

from arena import llm  # noqa: E402
from check_index import DATA, build, dev_runs  # noqa: E402
from harness import solvers  # noqa: E402
from harness.facts import world as facts_world  # noqa: E402
from harness.state import TaskRecord, answer_shape  # noqa: E402
from replay import tools as T  # noqa: E402
from replay.grade import grade  # noqa: E402

FAMILY_RX = {
    "compromised": re.compile(r"compromised in incident", re.I),
    "patient_zero": re.compile(r"entry point", re.I),
    "timeline": re.compile(r"\bA: .*\bB: ", re.S),
}


class Pacer:
    def __init__(self, tracker):
        self.tracker = tracker
        self.stopped = False

    def decide(self, state, qs, **kw):
        return llm.decide(state, qs, tracker=self.tracker)

    def chat(self, msgs, log=None, **kw):
        return llm.chat(msgs, tracker=self.tracker, **kw)

    def loop_stop_reason(self):
        return None


class Ctx:
    """What solvers and the facts runtime use of machine.Ctx."""

    def __init__(self, idx, tracker):
        self.index, self.client, self.run_id = idx, idx.client, idx.run_id
        self.tracker, self.pacer = tracker, Pacer(tracker)
        self.lock = threading.Lock()
        self.events: list = []

    def trace(self, task_id, event):
        self.events.append((task_id, event))


def family_of(req: str) -> str | None:
    return next((f for f, rx in FAMILY_RX.items() if rx.search(req or "")), None)


def main() -> int:
    args = sys.argv[1:]
    verbose = "-v" in args
    n = int(args[args.index("--runs") + 1]) if "--runs" in args else None
    only = args[args.index("--family") + 1] if "--family" in args else None
    tracker = llm.CostTracker()
    judge_cache = Path("/tmp") / "check_solvers_judge.json"
    stats = collections.defaultdict(lambda: collections.Counter())
    scores = collections.defaultdict(list)
    bad = []
    t0 = time.time()
    runs = dev_runs()[:n] if n else dev_runs()
    for rdir in runs:
        facts_world._CACHE.clear()
        idx = build(rdir)
        ctx = Ctx(idx, tracker)
        from harness.facts import runtime
        ctx._facts_judge = runtime.Judge(decide=lambda s, q, **kw: ctx.pacer.decide(s, q),
                                         chat=lambda m, **kw: ctx.pacer.chat(m, **kw), cache_file=judge_cache)
        manifest = json.loads((rdir / "manifest.json").read_text())
        files = {t["task_id"]: t["files"] for t in manifest["tasks"]}
        intel = json.loads((rdir / "intel.json").read_text()) if (rdir / "intel.json").exists() else {}
        for t in json.loads((rdir / "revealed.json").read_text())["tasks"]:
            req = t.get("request") or t["instruction"]
            fam = family_of(req)
            if not fam or (only and fam != only) or fam not in solvers.REGISTRY:
                continue
            rec = TaskRecord(task_id=t["task_id"], instruction=t["instruction"], level=t.get("level"))
            rec.request, rec.family, rec.intake_confident = req, fam, True
            rec.answer_shape = answer_shape(req)
            state = solvers.run(ctx, rec, enabled={fam})
            stats[fam]["n"] += 1
            if rec.draft is None:
                why = next((e.get("why") for tid, e in reversed(ctx.events)
                            if tid == t["task_id"] and e.get("event") == "solver"), "")
                bad.append((rdir.name, t["task_id"], fam, "not confident", why))
                scores[fam].append(0.0)
                continue
            stats[fam]["confident"] += 1
            ws = T.Workspace(files[t["task_id"]], DATA / "blobs", {}, intel)
            sub = {k: v for k, v in rec.draft.items() if k != "actions"}
            g = grade(t, sub, ws)
            scores[fam].append(g["score"])
            stats[fam]["score_sum"] += g["score"]
            if g["score"] < 0.99:
                bad.append((rdir.name, t["task_id"], fam, f"score {g['score']:.2f}", g.get("feedback"),
                            json.dumps(sub["answer"])[:150], json.dumps(t["expected_answer"])[:150]))
    print(f"{len(runs)} runs in {time.time() - t0:.0f}s, spend ${tracker.total:.4f}")
    print(f"{'family':<14} {'n':>4} {'confident':>10} {'score|conf':>11} {'family mean':>12}")
    for fam in sorted(stats):
        s = stats[fam]
        conf = s["confident"]
        print(f"{fam:<14} {s['n']:>4} {conf:>10} {s['score_sum'] / conf if conf else 0:>11.3f} "
              f"{sum(scores[fam]) / len(scores[fam]):>12.3f}")
    for b in bad[: (len(bad) if verbose else 15)]:
        print("   ", b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
