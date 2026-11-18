"""Family solvers (SPEC §5, build tickets 10-14): code computes a task's answer from the fact layer.

    REGISTRY["compromised"] = compromised.solve      # solve(ctx, rec, facts) -> Solution
    ENABLED = {"compromised", ...}                    # only families whose solver beats the loop on dev (by Score)

Recognition is two-part: intake picked the family with confidence (rec.intake_confident) AND the solver's own
preconditions pass (files and columns found, answer shape matches, facts ok). A solver signals a failed precondition
by returning `confident=False` (its partial facts go to the loop as notes) or by raising `Precondition`. Any other
exception is logged and sends the task to the loop too: a solver never ends a task by itself.

`run(ctx, rec)` is the SOLVE handler's body: confident → rec.draft, CHECK; otherwise notes → rec.notes, LOOP.
"""
from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..index import Unit
from ..state import State, TaskRecord


class Precondition(Exception):
    """A solver input is missing or ambiguous: the task goes to the loop."""


@dataclass
class Solution:
    answer: Any = None
    sources: list[dict] = field(default_factory=list)   # citations (locator dicts), one per item for lists
    actions: list[dict] = field(default_factory=list)   # state-changing actions for SUBMIT (allowlist-checked)
    confident: bool = False
    notes: list[dict] = field(default_factory=list)     # {"fact", "sources"}: seeds the loop when not confident
    outcome: str = "OK"
    why: str = ""                                       # why not confident (traced)


Solver = Callable[[Any, TaskRecord, Any], Solution]
REGISTRY: dict[str, Solver] = {}
ENABLED: set[str] = set()


def register(family: str, enabled: bool = True) -> Callable[[Solver], Solver]:
    """Decorator for `solve` in harness/solvers/<module>.py. `enabled` per the dev measurement (ticket record)."""
    def deco(fn: Solver) -> Solver:
        REGISTRY[family] = fn
        if enabled:
            ENABLED.add(family)
        else:
            ENABLED.discard(family)
        return fn
    return deco


def _load() -> None:
    for m in pkgutil.iter_modules(__path__):
        if not m.name.startswith("_"):
            importlib.import_module(f"{__name__}.{m.name}")


def note(fact: str, units: list[Unit | dict | None]) -> dict:
    """A loop note backed by evidence units or citation dicts (handle-less notes are dropped by the loop)."""
    return {"fact": fact, "sources": [u.source() if isinstance(u, Unit) else u for u in units if u]}


def run(ctx: Any, rec: TaskRecord, registry: dict[str, Solver] | None = None,
        enabled: set[str] | None = None) -> State:
    reg = REGISTRY if registry is None else registry
    on = ENABLED if enabled is None else enabled
    fam = rec.family
    if not rec.intake_confident or fam not in reg or fam not in on:
        return State.LOOP
    log = lambda e: ctx.trace(rec.task_id, e)  # noqa: E731
    try:
        from ..facts.runtime import facts_of
        sol = reg[fam](ctx, rec, lambda need=("artifacts", "settings"): facts_of(ctx, rec.task_id, need, log))
    except Precondition as e:
        log({"event": "solver", "family": fam, "confident": False, "why": f"precondition: {e}"})
        return State.LOOP
    except Exception as e:  # noqa: BLE001 - a broken solver must not cost the task: the loop takes over
        log({"event": "solver", "family": fam, "confident": False, "why": f"{type(e).__name__}: {e}"})
        rec.errors.append(f"SOLVE {fam}: {type(e).__name__}: {e}")
        return State.LOOP
    log({"event": "solver", "family": fam, "confident": sol.confident, "why": sol.why,
         "answer": sol.answer if sol.confident else None, "sources": len(sol.sources), "actions": len(sol.actions)})
    if sol.confident:
        # citations are locator dicts: a solver that hands over an evidence Unit gets it converted, not a crash at submit
        srcs = [s.source() if isinstance(s, Unit) else s for s in sol.sources if s]
        rec.draft = {"outcome": sol.outcome, "answer": sol.answer, "sources": srcs, "actions": sol.actions}
        return State.CHECK
    rec.notes += [n for n in sol.notes if n.get("sources")]
    return State.LOOP


_load()
