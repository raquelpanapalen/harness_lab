"""Offline tests of the solver framework (build ticket 10): SOLVE routing, confident drafts, precondition failures and
crashes falling back to the loop, notes seeding the notebook. No model calls.

    uv run tests/test_solvers.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import solvers  # noqa: E402
from harness.solvers import Precondition, Solution  # noqa: E402
from harness.state import State, TaskRecord  # noqa: E402


class Ctx:
    def __init__(self):
        self.events = []
        self.index = None

    def trace(self, task_id, event):
        self.events.append(event)


def rec(family="fam", confident=True):
    r = TaskRecord(task_id="t1", instruction="x")
    r.family, r.intake_confident = family, confident
    return r


def test_routing():
    ctx = Ctx()
    reg = {"fam": lambda c, r, f: Solution(answer=["a"], sources=[{"path": "/x", "line": 1}], confident=True)}
    r = rec()
    assert solvers.run(ctx, r, reg, {"fam"}) is State.CHECK
    assert r.draft == {"outcome": "OK", "answer": ["a"], "sources": [{"path": "/x", "line": 1}], "actions": []}
    assert solvers.run(ctx, rec(confident=False), reg, {"fam"}) is State.LOOP     # intake unsure
    assert solvers.run(ctx, rec(), reg, set()) is State.LOOP                     # solver disabled
    assert solvers.run(ctx, rec(family="other"), reg, {"fam"}) is State.LOOP     # no solver


def test_unconfident_seeds_notes():
    ctx = Ctx()
    reg = {"fam": lambda c, r, f: Solution(answer=["a"], confident=False, why="facts not ok",
                                           notes=[{"fact": "a did x", "sources": [{"path": "/x", "line": 3}]},
                                                  {"fact": "no evidence", "sources": []}])}
    r = rec()
    assert solvers.run(ctx, r, reg, {"fam"}) is State.LOOP
    assert r.draft is None and r.notes == [{"fact": "a did x", "sources": [{"path": "/x", "line": 3}]}]
    assert ctx.events[-1]["why"] == "facts not ok"


def test_precondition_and_crash_go_to_loop():
    def pre(c, r, f):
        raise Precondition("roster not found")

    def crash(c, r, f):
        raise KeyError("boom")

    for fn, why in ((pre, "precondition: roster not found"), (crash, "KeyError")):
        ctx, r = Ctx(), rec()
        assert solvers.run(ctx, r, {"fam": fn}, {"fam"}) is State.LOOP
        assert why in ctx.events[-1]["why"] and r.draft is None


def test_forced_precondition_failure_still_submits():
    """End to end through the state machine: a solver whose precondition fails goes to the loop, and the task is
    still submitted (the loop is stubbed to hand over to FALLBACK_SUBMIT's empty answer)."""
    from harness import machine

    sent = []

    class Client:
        def submit(self, run_id, task_id, outcome, answer, sources=None):
            sent.append((outcome, answer))
            return {"received": True}

    import tempfile

    from arena import llm
    ctx = machine.Ctx(client=Client(), run_id="r", out_dir=Path(tempfile.mkdtemp()), tracker=llm.CostTracker(),
                      deadlines=machine.Deadlines())

    def pre(c, r, f):
        raise Precondition("forced")

    handlers = dict(machine.HANDLERS)
    handlers[State.INTAKE] = lambda c, r: (setattr(r, "family", "fam"), setattr(r, "intake_confident", True),
                                           State.SOLVE)[-1]
    handlers[State.SOLVE] = lambda c, r: solvers.run(c, r, {"fam": pre}, {"fam"})
    handlers[State.LOOP] = lambda c, r: State.FALLBACK_SUBMIT
    r = TaskRecord(task_id="t9", instruction="List them as a JSON array.")
    out = machine.run_task(ctx, r, handlers) if "stop_before" not in machine.run_task.__code__.co_varnames else \
        machine.run_task(ctx, r, handlers, frozenset())
    assert out.state is State.DONE and out.submitted and sent == [("OK", [])], (out.state, sent, out.errors)


def test_unit_sources_become_locators():
    import json

    from harness.index import Unit
    u = Unit(path="/a.csv", kind="record", loc={"record": "R-1"}, text="R-1,x")
    reg = {"fam": lambda c, r, f: Solution(answer=["a"], sources=[u, {"path": "/b"}, None], confident=True)}
    r = rec()
    solvers.run(Ctx(), r, reg, {"fam"})
    assert r.draft["sources"] == [{"path": "/a.csv", "record": "R-1"}, {"path": "/b"}]
    json.dumps(r.draft)


def test_registry_loads_modules():
    assert isinstance(solvers.REGISTRY, dict) and solvers.ENABLED <= set(solvers.REGISTRY)


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
