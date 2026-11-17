"""Offline checks of the lab harness state machine (build ticket 02): python tests/test_lab_machine.py

Every task ends DONE with exactly one submission, whichever state fails, on the hard deadline, after a restart, and
when the platform rejects or already has the submission.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import harness  # noqa: E402
from arena import llm  # noqa: E402
from arena.client import ArenaError, Run, Task  # noqa: E402
from harness.machine import HANDLERS, Ctx, Deadlines, run_task  # noqa: E402
from harness.pacing import Pacer  # noqa: E402
from harness.state import State, TaskRecord, answer_shape, empty_submission  # noqa: E402

# No model calls in these tests: intake's Jev call answers "compromised" with certainty.
JEV_ANSWER = {"family": {"type": "choice", "choice": "compromised", "probabilities": {"compromised": 0.99}}}
Pacer.decide = lambda self, state, questions, **kw: JEV_ANSWER  # type: ignore[method-assign]


class FakeClient:
    base_url = "http://fake"

    def __init__(self, fail_with: list[ArenaError] | None = None, files: dict[str, str] | None = None):
        self.submits: list[tuple] = []
        self.fail_with = list(fail_with or [])
        self.files = files or {}
        self.throttle = None

    def task_manifest(self, run_id, task_id, refresh=False):
        return {p: {"path": p} for p in self.files}

    def download_file(self, run_id, task_id, path):
        return self.files[path].encode(), "text/plain"

    def submit(self, run_id, task_id, outcome, answer, sources=None):
        if self.fail_with:
            raise self.fail_with.pop(0)
        self.submits.append((task_id, outcome, answer, sources))
        return {"received": True, "task_id": task_id, "feedback": ["ok"]}


def ctx(tmp: Path, client=None, **dl) -> Ctx:
    return Ctx(client=client or FakeClient(), run_id="r1", out_dir=tmp, tracker=llm.CostTracker(),
               deadlines=Deadlines(**dl))


# A full happy path through every state, so a failure can be injected into each one.
DRAFT = {"outcome": "OK", "answer": ["x"], "sources": []}


def full_path(fail_in: State | None) -> dict:
    def mk(nxt, draft=False):
        def h(c, rec):
            if rec.state is fail_in:
                raise RuntimeError(f"boom in {rec.state.value}")
            if draft:
                rec.draft = dict(DRAFT)
            return nxt
        return h
    seen = {"repair": 0}

    def check(c, rec):
        if rec.state is fail_in:
            raise RuntimeError("boom in CHECK")
        seen["repair"] += 1
        return State.REPAIR if seen["repair"] == 1 else State.SUBMIT

    return {**HANDLERS, State.INTAKE: mk(State.SOLVE), State.SOLVE: mk(State.LOOP), State.LOOP: mk(State.COMPOSE),
            State.COMPOSE: mk(State.CHECK, draft=True), State.CHECK: check, State.REPAIR: mk(State.CHECK),
            State.SUBMIT: (lambda c, rec: (_ for _ in ()).throw(RuntimeError("boom in SUBMIT")))
            if fail_in is State.SUBMIT else HANDLERS[State.SUBMIT]}


def test_default_path_submits_empty_right_shaped() -> None:
    with tempfile.TemporaryDirectory() as d:
        c = ctx(Path(d))
        rec = run_task(c, TaskRecord("t01", "List the ids as a sorted JSON array.", 1))
        assert rec.state is State.DONE and rec.submitted, rec
        assert c.client.submits == [("t01", "OK", [], [])], c.client.submits
        assert TaskRecord.load(Path(d) / "state" / "t01.json").state is State.DONE


def test_failure_in_every_state() -> None:
    for fail_in in [None, *[s for s in State if s not in (State.FALLBACK_SUBMIT, State.DONE)]]:
        with tempfile.TemporaryDirectory() as d:
            c = ctx(Path(d))
            rec = run_task(c, TaskRecord("t01", "Answer as JSON {\"a\": 1}.", 2), full_path(fail_in))
            assert rec.state is State.DONE and len(c.client.submits) == 1, (fail_in, c.client.submits, rec.errors)
            sub = c.client.submits[0]
            if fail_in in (None, State.CHECK, State.REPAIR, State.SUBMIT):
                assert sub[2] == ["x"], (fail_in, sub)       # the draft survives later failures
            else:
                assert sub[2] == {}, (fail_in, sub)          # no draft yet: right-shaped empty object
            if fail_in:
                assert any(fail_in.value in e for e in rec.errors), rec.errors


def test_fallback_submit_failure_terminates() -> None:
    with tempfile.TemporaryDirectory() as d:
        c = ctx(Path(d), FakeClient([ArenaError("bad request", 400)]))
        rec = run_task(c, TaskRecord("t01", "x", 1))
        assert rec.state is State.DONE and not rec.submitted and rec.errors, rec


def test_transient_submit_errors_are_retried_and_409_counts() -> None:
    with tempfile.TemporaryDirectory() as d:
        c = ctx(Path(d), FakeClient([ArenaError("busy", 503)]))
        assert run_task(c, TaskRecord("t01", "x", 1)).submitted and len(c.client.submits) == 1
    with tempfile.TemporaryDirectory() as d:
        c = ctx(Path(d), FakeClient([ArenaError("task already submitted", 409)]))
        rec = run_task(c, TaskRecord("t01", "x", 1))
        assert rec.submitted and rec.state is State.DONE and not c.client.submits


def test_hard_deadline_forces_fallback() -> None:
    with tempfile.TemporaryDirectory() as d:
        c = ctx(Path(d), start=time.time() - 3600)
        rec = run_task(c, TaskRecord("t01", "x", 1), full_path(None))
        assert rec.state is State.DONE and len(c.client.submits) == 1
        assert rec.transitions[0]["to"] == "FALLBACK_SUBMIT" and rec.transitions[0]["note"] == "hard deadline"


def test_handler_that_never_advances() -> None:
    with tempfile.TemporaryDirectory() as d:
        c = ctx(Path(d))
        rec = run_task(c, TaskRecord("t01", "x", 1), {**HANDLERS, State.SOLVE: lambda c, r: State.SOLVE})
        assert rec.state is State.DONE and len(c.client.submits) == 1


def test_resume_after_restart() -> None:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        sd = out / "state"
        # t01 was mid-run (in LOOP) when the process died; t02 was done; t03 never started.
        r1 = TaskRecord("t01", "List them as a JSON array.", 1, state=State.LOOP, answer_shape="list")
        r1.transitions = [{"from": "INTAKE", "to": "SOLVE"}, {"from": "SOLVE", "to": "LOOP"}]
        r1.save(sd)
        TaskRecord("t02", "x", 1, state=State.DONE, submitted=True).save(sd)
        client = FakeClient()
        run = Run(run_id="r1", kind="dev")
        tasks = [Task(task_id=t, instruction="List them as a JSON array.", level=1) for t in ("t01", "t02", "t03")]
        res = harness.run(client, run, tasks, llm.CostTracker(), out, Deadlines())
        assert sorted(s[0] for s in client.submits) == ["t01", "t03"], client.submits
        assert res["t02"] == {"feedback": "submitted earlier"}
        rec = TaskRecord.load(sd / "t01.json")
        assert [t["to"] for t in rec.transitions][2] == "FALLBACK_SUBMIT", rec.transitions  # continued from LOOP
        assert (out / "t01.jsonl").read_text().count('"event": "resume"') == 1


def test_answer_shapes() -> None:
    assert answer_shape('Answer as JSON {"artifact": "<path>"}.') == "object"
    assert answer_shape("Return every violating request_id as a sorted JSON array.") == "list"
    assert answer_shape("Pick up your open ticket in /tickets/ and submit what it asks for.") == "unknown"
    assert empty_submission("list") == {"outcome": "OK", "answer": [], "sources": []}
    assert empty_submission("unknown")["answer"] is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("lab machine tests ok")
