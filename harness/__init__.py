"""The new Harness Lab harness (spec: wayfinder/harness/SPEC.md; build tickets: wayfinder/harness/build/).

Entry point for `main.py --harness lab`: `run(...)` solves the selected tasks of a started run and returns one result
per task id; main.py finishes the run and prints the summary.
"""
from __future__ import annotations

from pathlib import Path

from arena import llm
from arena.client import ArenaClient, Run, Task


def run(client: ArenaClient, run: Run, tasks: list[Task], tracker: llm.CostTracker, out_dir: Path) -> dict[str, dict]:
    """Stub (build ticket 01): submits nothing. Build ticket 02 replaces it with the per-task state machine."""
    print(f"lab harness: stub, {len(tasks)} task(s) left unsubmitted")
    return {t.task_id: {"feedback": "not submitted (lab harness stub)"} for t in tasks}
