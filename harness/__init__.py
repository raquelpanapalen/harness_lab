"""The new Harness Lab harness (spec: wayfinder/harness/SPEC.md; build tickets: wayfinder/harness/build/).

Entry point for `main.py --harness lab`: `run(...)` drives every selected task of a started run through the per-task
state machine (harness.machine) and returns one result per task id; main.py finishes the run and prints the summary.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from arena import llm
from arena.client import ArenaClient, Run, Task

from .machine import HANDLERS, Ctx, Deadlines, Handler, run_task
from .pacing import Budget, Pacer
from .state import State, TaskRecord

# Task threads. Model and Task API concurrency is capped per endpoint by the pacer (harness/pacing.py), so this only
# bounds how many tasks are open at once.
WORKERS = 16


def run_clock(state_dir: Path) -> Deadlines:
    """The run's deadlines count from its first start, also after a process restart (state/_run.json)."""
    f = state_dir / "_run.json"
    if f.exists():
        return Deadlines(start=json.loads(f.read_text())["started"])
    state_dir.mkdir(parents=True, exist_ok=True)
    d = Deadlines()
    f.write_text(json.dumps({"started": d.start}))
    return d


def load_record(state_dir: Path, task: Task) -> TaskRecord:
    f = state_dir / f"{task.task_id}.json"
    if f.exists():
        return TaskRecord.load(f)
    return TaskRecord(task_id=task.task_id, instruction=task.instruction, level=task.level)


def run(client: ArenaClient, run: Run, tasks: list[Task], tracker: llm.CostTracker, out_dir: Path,
        deadlines: Deadlines | None = None, budget: Budget | None = None, pacer: Pacer | None = None,
        handlers: dict[State, Handler] = HANDLERS) -> dict[str, dict]:
    """Two phases (SPEC §8 timeline): first every task runs up to LOOP, so the ones a solver answers confidently are
    submitted first; then the parked tasks run their loops. Both phases share the pacer's caps and run limits."""
    deadlines = deadlines or run_clock(out_dir / "state")
    budget = budget or Budget()
    ctx = Ctx(client=client, run_id=run.run_id, out_dir=out_dir, tracker=tracker, deadlines=deadlines,
              budget=budget, pacer=pacer or Pacer(tracker, deadlines, budget))
    assert ctx.pacer is not None
    client.throttle = ctx.pacer.task_api.acquire
    t0s: dict[str, float] = {}

    def work(task: Task, stop_before: frozenset[State]) -> tuple[str, TaskRecord]:
        t0s.setdefault(task.task_id, time.time())
        rec = load_record(ctx.state_dir, task)
        if rec.state is not State.DONE and rec.state not in stop_before:
            if rec.transitions and task.task_id not in ctx.started:
                ctx.trace(task.task_id, {"event": "resume", "state": rec.state.value})
            ctx.started.add(task.task_id)
            rec = run_task(ctx, rec, handlers, stop_before)
        if rec.state is State.DONE:
            res = _result(rec)
            print(f"  {task.task_id} done in {time.time() - t0s[task.task_id]:.0f}s: {_line(res)}")
        return task.task_id, rec

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        first = dict(ex.map(lambda t: work(t, frozenset({State.LOOP})), tasks))
        parked = [t for t in tasks if first[t.task_id].state is not State.DONE]
        if parked:
            print(f"  {len(tasks) - len(parked)} task(s) done in phase 1; {len(parked)} go to the loop")
        recs = {**first, **dict(ex.map(lambda t: work(t, frozenset()), parked))}
    return {tid: _result(rec) for tid, rec in recs.items()}


def _result(rec: TaskRecord) -> dict:
    if rec.result:
        return rec.result
    if rec.submitted:
        return {"feedback": "submitted earlier"}
    return {"error": "; ".join(rec.errors[-2:]) or "not submitted"}


def _line(res: dict) -> str:
    fb = res.get("feedback") or res.get("error")
    return " | ".join(map(str, fb)) if isinstance(fb, list) else str(fb)


# --- run-level resume -----------------------------------------------------------------------------------------------
# runs/ACTIVE.json names the run this harness is working on. A process that restarts while that run is still active
# (same platform, same kind) continues it instead of starting a new one; main.py then skips submitted tasks and
# run() resumes the rest from their records.
ACTIVE = Path("runs") / "ACTIVE.json"


def active_run(client: ArenaClient, kind: str) -> Run | None:
    try:
        a = json.loads(ACTIVE.read_text())
    except (OSError, ValueError):
        return None
    if a.get("base_url") != client.base_url or a.get("kind") != kind:
        return None
    try:
        d = client.get_run(a["run_id"])
    except Exception:  # noqa: BLE001 - unknown or unreachable: start fresh
        return None
    return Run(**d) if d.get("status") == "active" else None


def mark_active(client: ArenaClient, run: Run) -> None:
    ACTIVE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE.write_text(json.dumps({"run_id": run.run_id, "kind": run.kind, "base_url": client.base_url}))


def clear_active(run: Run) -> None:
    try:
        if json.loads(ACTIVE.read_text()).get("run_id") == run.run_id:
            ACTIVE.unlink()
    except (OSError, ValueError):
        pass
