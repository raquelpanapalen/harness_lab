"""The per-task state machine (SPEC §2): one handler per state returns the next state.

    INTAKE → SOLVE ─┬─ solver confident ─────┐
                    └─ else → LOOP ──────────┤
                                             ▼
                    COMPOSE → CHECK ──ok──→ SUBMIT → DONE
                                └ fail → REPAIR → CHECK (once)
    any error, deadline or budget limit ──────────→ FALLBACK_SUBMIT → DONE

Every handler that raises sends the task to FALLBACK_SUBMIT; the hard deadline does too. FALLBACK_SUBMIT always ends
in DONE, so `run_task` terminates whatever the handlers do. States whose stage isn't built yet go to FALLBACK_SUBMIT.
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arena import llm
from arena.client import ArenaClient, ArenaError

from . import compose as compose_mod
from . import intake as intake_mod
from . import loop as loop_mod
from . import guards
from . import solvers, vision
from .pacing import Budget, Pacer
from .state import State, TaskRecord, answer_shape, empty_submission

SUBMIT_TRIES = 4


@dataclass
class Deadlines:
    """Run-level clock. Defaults follow SPEC §8: soft 24 min (no new loop steps), hard 27 min (fallback-submit all)."""
    start: float = field(default_factory=time.time)
    soft_s: float = 24 * 60
    hard_s: float = 27 * 60

    def soft_passed(self) -> bool:
        return time.time() - self.start >= self.soft_s

    def hard_passed(self) -> bool:
        return time.time() - self.start >= self.hard_s


@dataclass
class Ctx:
    client: ArenaClient
    run_id: str
    out_dir: Path
    tracker: llm.CostTracker
    deadlines: Deadlines
    budget: Budget = field(default_factory=Budget)
    pacer: Pacer | None = None  # built from tracker, deadlines and budget unless given
    lock: threading.Lock = field(default_factory=threading.Lock)
    started: set[str] = field(default_factory=set)  # tasks this process has driven (phase 2 is not a "resume")
    index: Any = None  # harness.index.Index built at SETUP (None if setup failed: tools then go to the server)
    tools: Any = None  # harness.tools.Tools: read-only tools from the index, the rest from the server

    def __post_init__(self) -> None:
        if self.pacer is None:
            self.pacer = Pacer(self.tracker, self.deadlines, self.budget)

    @property
    def state_dir(self) -> Path:
        return self.out_dir / "state"

    def trace(self, task_id: str, event: dict) -> None:
        """One jsonl line per event in runs/<run>/<task>.jsonl; `cost_total` lets report.py read the run's spend."""
        line = json.dumps({"t": round(time.time() - self.deadlines.start, 2), **event,
                           "cost_total": round(self.tracker.total, 8)}, ensure_ascii=False, default=str)
        with self.lock, open(self.out_dir / f"{task_id}.jsonl", "a") as f:
            f.write(line + "\n")


Handler = Callable[[Ctx, TaskRecord], State]


# --- handlers -------------------------------------------------------------------------------------------------------
def intake(ctx: Ctx, rec: TaskRecord) -> State:
    """Resolve the request (open ticket, inbox message), read the trusted rules, one Jev call for family, kind and
    allowlist (harness/intake.py). The answer shape comes from the resolved request, which states the format."""
    paths = _paths(ctx, rec)

    def read(path: str) -> str:
        if ctx.tools is not None:
            return ctx.tools.read_text(rec.task_id, path) or ""
        return ctx.client.download_file(ctx.run_id, rec.task_id, path)[0].decode("utf-8", "replace")

    log = lambda e: ctx.trace(rec.task_id, e)  # noqa: E731
    vis = vision.of(ctx)
    req = intake_mod.resolve_request(rec.instruction, paths, read, lambda p: vis.transcribe(rec.task_id, p, log))
    if req.text:  # images the request names (scanned pages, screenshots) are read now
        vis.transcribe_named(rec.task_id, req.text, paths, log)
    rec.request, rec.request_via, rec.request_source = req.text, req.via, req.source
    rec.inbox, rec.unread_images = list(req.messages), req.unread_images
    rec.answer_shape = answer_shape(req.text or rec.instruction)
    rec.rules, rec.operator_files = intake_mod.trusted_rules(paths, read)
    assert ctx.pacer is not None
    it = intake_mod.classify(req, lambda st, qs: ctx.pacer.decide(st, qs, log=lambda e: ctx.trace(rec.task_id, e)))
    rec.family, rec.family_confidence, rec.intake_confident = it.family, round(it.confidence, 4), it.confident
    rec.task_kind, rec.inbox_action, rec.allowlist = it.kind, it.action, it.allowlist
    ctx.trace(rec.task_id, {"event": "intake", "family": it.family, "confidence": rec.family_confidence,
                            "kind": it.kind, "allowlist": it.allowlist, "via": req.via, "source": req.source,
                            "unread_images": len(req.unread_images)})
    return State.SOLVE


def _paths(ctx: Ctx, rec: TaskRecord) -> list[str]:
    """The task's workspace paths: from the index when SETUP built it, else the platform's manifest."""
    if ctx.index is not None and rec.task_id in ctx.index.files:
        return list(ctx.index.view(rec.task_id).paths())
    try:
        return list(ctx.client.task_manifest(ctx.run_id, rec.task_id))
    except ArenaError as e:
        if e.status != 404:
            raise
        return []  # platform without manifests: tickets and inbox can't be listed; the instruction is the request


def solve(ctx: Ctx, rec: TaskRecord) -> State:
    """Family solvers (harness/solvers): only when intake is confident, the family's solver is enabled and its own
    preconditions pass; a confident answer goes to CHECK, anything else (with the solver's notes) to the loop."""
    return solvers.run(ctx, rec)


def loop(ctx: Ctx, rec: TaskRecord) -> State:
    """One notebook-loop step (harness/loop.py); LOOP again until done or the step cap, then COMPOSE."""
    return loop_mod.step(ctx, rec)


def compose(ctx: Ctx, rec: TaskRecord) -> State:
    rec.draft = compose_mod.compose(ctx, rec)
    return State.CHECK


def check(ctx: Ctx, rec: TaskRecord) -> State:
    """The guards (harness/guards.py): fixable problems get one repair round; afterwards the cleaned draft (blocked
    parts removed) is submitted."""
    if not rec.draft:
        return State.FALLBACK_SUBMIT
    clean, problems = guards.check(ctx, rec, rec.draft)
    rec.check_problems = [p for p in problems if not p.startswith("(")]
    ctx.trace(rec.task_id, {"event": "check", "problems": problems, "repairs": rec.repairs})
    if rec.check_problems and rec.repairs == 0 and ctx.pacer is not None and not ctx.pacer.loop_stop_reason():
        return State.REPAIR
    rec.draft = clean
    return State.SUBMIT


def repair(ctx: Ctx, rec: TaskRecord) -> State:
    rec.repairs += 1
    rec.draft = compose_mod.compose(ctx, rec, feedback=rec.check_problems)
    return State.CHECK


def submit(ctx: Ctx, rec: TaskRecord) -> State:
    """Run the draft's allowed actions (once, also across a restart), then submit the answer."""
    if not rec.draft:
        return State.FALLBACK_SUBMIT
    for i, a in enumerate(rec.draft.get("actions") or []):
        if i < len(rec.actions_done):
            continue  # already sent before a restart
        res = ctx.tools.call(rec.task_id, a["tool"], a["args"]) if ctx.tools is not None else \
            ctx.client.call_tool(ctx.run_id, rec.task_id, a["tool"], a["args"])
        rec.actions_done.append({"action": a, "result": res})
        rec.save(ctx.state_dir)
        ctx.trace(rec.task_id, {"event": "action", "tool": a["tool"], "args": a["args"], "result": res})
    _send(ctx, rec, {k: v for k, v in rec.draft.items() if k != "actions"})
    return State.DONE


def fallback_submit(ctx: Ctx, rec: TaskRecord) -> State:
    """The fallback ladder (SPEC §7): the best draft so far, else a compose from the notebook (no actions), else a
    right-shaped empty answer. Always ends in DONE."""
    shape = rec.answer_shape if rec.answer_shape != "unknown" else answer_shape(rec.instruction)  # INTAKE may have failed
    if not rec.draft and rec.notes and ctx.pacer is not None and not ctx.pacer.stopped \
            and not ctx.deadlines.hard_passed():
        try:
            rec.draft = compose_mod.compose(ctx, rec, with_actions=False)
        except Exception as e:  # noqa: BLE001 - next rung
            rec.errors.append(f"FALLBACK_SUBMIT compose: {type(e).__name__}: {e}")
    sub = {k: v for k, v in rec.draft.items() if k != "actions"} if rec.draft else empty_submission(shape)
    if sub.get("answer") is not None:  # outgoing text is rewritten on every path (defang, no secrets)
        sub["answer"] = guards.rewrite_value(sub["answer"], set(), rec.rules or "")
    try:
        _send(ctx, rec, sub)
    except Exception as e:  # noqa: BLE001 - last rung: the right-shaped empty answer
        rec.errors.append(f"FALLBACK_SUBMIT: {type(e).__name__}: {e}")
        if not rec.submitted and sub != empty_submission(shape):
            try:
                _send(ctx, rec, empty_submission(shape))
            except Exception as e2:  # noqa: BLE001 - nothing left to fall back to; record it and stop
                rec.errors.append(f"FALLBACK_SUBMIT empty: {type(e2).__name__}: {e2}")
    return State.DONE


def _clean_sources(sources) -> list[dict]:
    """Locator dicts only: evidence units become their locator, anything else is dropped (a submission must always
    serialise)."""
    out = []
    for s in sources or []:
        if hasattr(s, "source"):
            s = s.source()
        if isinstance(s, dict) and s.get("path") and s not in out:
            out.append({k: v for k, v in s.items() if isinstance(v, (str, int, float))})
    return out


def _send(ctx: Ctx, rec: TaskRecord, sub: dict) -> None:
    """Submit once, retrying transport errors; a 409 means the platform already has a submission for this task."""
    sub = {**sub, "sources": _clean_sources(sub.get("sources"))}
    json.dumps(sub["answer"])  # fail here, not half-way, if the answer can't be sent
    for i in range(SUBMIT_TRIES):
        try:
            rec.result = ctx.client.submit(ctx.run_id, rec.task_id, sub["outcome"], sub["answer"], sub.get("sources"))
            rec.submitted = True
            ctx.trace(rec.task_id, {"event": "submit", "submission": sub, "result": rec.result})
            return
        except ArenaError as e:
            if e.status == 409:
                rec.submitted = True
                rec.errors.append(f"submit: already submitted ({e})")
                return
            if e.status is not None and e.status < 500 and e.status != 429 or i == SUBMIT_TRIES - 1:
                raise
            time.sleep(2 ** i)


HANDLERS: dict[State, Handler] = {
    State.INTAKE: intake,
    State.SOLVE: solve,
    State.LOOP: loop,
    State.COMPOSE: compose,
    State.CHECK: check,
    State.REPAIR: repair,
    State.SUBMIT: submit,
    State.FALLBACK_SUBMIT: fallback_submit,
}


# --- driver ---------------------------------------------------------------------------------------------------------
def run_task(ctx: Ctx, rec: TaskRecord, handlers: dict[State, Handler] = HANDLERS,
             stop_before: frozenset[State] = frozenset()) -> TaskRecord:
    """Drive one task to DONE (or until it reaches a state in `stop_before`), saving the record after every
    transition. The pacer's run limits apply before each handler: a 403, the hard deadline or the hard budget send the
    task to FALLBACK_SUBMIT; the soft deadline or soft budget turn LOOP into COMPOSE (no new loop steps). The LOOP
    handler must also check `ctx.pacer.loop_stop_reason()` before every step it takes."""
    rec.save(ctx.state_dir)
    while rec.state != State.DONE and rec.state not in stop_before:
        cur = rec.state
        assert ctx.pacer is not None
        if cur is not State.FALLBACK_SUBMIT and (why := ctx.pacer.fallback_reason()):
            nxt, note = State.FALLBACK_SUBMIT, why
        elif cur is State.LOOP and (why := ctx.pacer.loop_stop_reason()):
            nxt, note = State.COMPOSE, why
        else:
            rec.attempts[cur.value] = rec.attempts.get(cur.value, 0) + 1
            try:
                nxt, note = handlers[cur](ctx, rec), ""
            except Exception as e:  # noqa: BLE001 - any failure takes the fallback edge
                rec.errors.append(f"{cur.value}: {type(e).__name__}: {e}")
                nxt, note = (State.DONE if cur is State.FALLBACK_SUBMIT else State.FALLBACK_SUBMIT), f"error in {cur.value}"
            if nxt is cur and rec.attempts[cur.value] > 50:  # a handler that never advances must not spin forever
                nxt, note = State.FALLBACK_SUBMIT, f"{cur.value} did not advance"
        rec.move(nxt, note)
        rec.save(ctx.state_dir)
        ctx.trace(rec.task_id, {"event": "transition", "from": cur.value, "to": nxt.value, **({"note": note} if note else {})})
    return rec
