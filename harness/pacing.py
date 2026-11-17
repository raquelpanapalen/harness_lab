"""Pacing and resilience (SPEC §8, build ticket 03): one scheduler for every model and Task API call.

- **Concurrency caps per endpoint:** Gemma ≤ 6 in flight (a 429 halves the limit; +1 per error-free minute back up to
  the cap), Jev ≤ 10, embeddings ≤ 2. The Task API shares one 15 requests/s allowance (`ArenaClient.throttle`).
- **Retries by error type**, bounded by the task's deadline rather than an attempt count:
  429 / 5xx / 408 / timeouts / network errors / empty responses → exponential backoff with full jitter, at least the
  Retry-After; 402 with Retry-After (OpenRouter's in-flight budget) the same.
  400 → retry once as is, then ask the caller to shrink its context (`shrink()`), else raise `NeedsShrink`.
  403 → the $3 limit: stop all model calls (`BudgetExhausted`) and send every open task to FALLBACK_SUBMIT.
  Every failure is logged with its status, provider and error body.
- **Run limits:** soft deadline 24 min / $0.50 spent → no new loop steps; hard deadline 27 min / $1.00 spent / a 403 →
  FALLBACK_SUBMIT for every open task. All thresholds are constructor arguments (tests), defaulting to the spec.

Model calls made through `Pacer.chat` / `decide` / `embed` run with the SDK's own retries off (`retries=0`) and a
per-attempt timeout no longer than the time left, so the pacer alone decides when to try again.
"""
from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from arena import llm

T = TypeVar("T")
Log = Callable[[dict], None]


class PacingStop(Exception):
    """A model call that must not (or can no longer) be made; the state machine takes the fallback edge."""


class BudgetExhausted(PacingStop):
    """A 403 from OpenRouter: the key's limit is reached. Every later model call fails the same way."""


class DeadlineExceeded(PacingStop):
    """The call could not succeed before its deadline (waiting for a slot or backing off would pass it)."""


class NeedsShrink(Exception):
    """A 400 came back twice: the caller must shrink its context (halve the tool result, condense the notebook)."""


@dataclass
class Budget:
    """Run budget watcher (SPEC §8): no new loop steps from `soft_usd`, FALLBACK_SUBMIT for open tasks from `hard_usd`."""
    soft_usd: float = 0.50
    hard_usd: float = 1.00


# endpoint -> (cap, adaptive)
CAPS: dict[str, tuple[int, bool]] = {"gemma": (6, True), "jev": (10, False), "embed": (2, False)}
TASK_API_RPS = 15.0
CALL_TIMEOUT = 90.0  # per attempt; never longer than the time left before the deadline


class Slots:
    """Concurrency limit for one endpoint. Adaptive: a 429 halves the limit (not below 1); each minute without an
    error raises it by 1, up to the cap."""

    def __init__(self, cap: int, adaptive: bool = False, grow_every: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        self.cap, self.adaptive, self.grow_every, self.clock = cap, adaptive, grow_every, clock
        self.limit = cap
        self.in_flight = 0
        self.peak = 0
        self._last_change = clock()
        self._cond = threading.Condition()

    def _grow(self) -> None:  # caller holds the lock
        if not self.adaptive or self.limit >= self.cap:
            return
        steps = int((self.clock() - self._last_change) // self.grow_every)
        if steps > 0:
            self.limit = min(self.cap, self.limit + steps)
            self._last_change += steps * self.grow_every
            self._cond.notify_all()

    def acquire(self, deadline: float) -> None:
        with self._cond:
            while True:
                self._grow()
                if self.in_flight < self.limit:
                    self.in_flight += 1
                    self.peak = max(self.peak, self.in_flight)
                    return
                left = deadline - time.time()
                if left <= 0:
                    raise DeadlineExceeded("no free slot before the deadline")
                self._cond.wait(min(left, 1.0))

    def release(self) -> None:
        with self._cond:
            self.in_flight -= 1
            self._cond.notify_all()

    def error(self, status: int | None) -> None:
        """Any error restarts the error-free minute; a 429 also halves the limit."""
        with self._cond:
            if self.adaptive and status == 429:
                self.limit = max(1, self.limit // 2)
            self._last_change = self.clock()


class RateLimit:
    """Token bucket: `rate` requests per second, bursts up to `rate`. Shared by every Task API call."""

    def __init__(self, rate: float = TASK_API_RPS):
        self.rate = rate
        self._tokens = rate
        self._at = time.monotonic()
        self._lock = threading.Lock()
        self.count = 0

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.rate, self._tokens + (now - self._at) * self.rate)
                self._at = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    self.count += 1
                    return
                wait = (1 - self._tokens) / self.rate
            time.sleep(wait)


def _retryable(e: llm.LLMError) -> bool:
    s = e.status
    return s is None or s in (408, 429) or s >= 500 or (s == 402 and e.retry_after > 0)


class Pacer:
    def __init__(self, tracker: llm.CostTracker, deadlines: Any, budget: Budget | None = None,
                 caps: dict[str, tuple[int, bool]] | None = None, task_api_rps: float = TASK_API_RPS,
                 call_timeout: float = CALL_TIMEOUT, backoff_base: float = 1.0, backoff_cap: float = 30.0,
                 grow_every: float = 60.0, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.tracker, self.deadlines, self.budget = tracker, deadlines, budget or Budget()
        self.slots = {name: Slots(cap, adaptive, grow_every, clock) for name, (cap, adaptive) in (caps or CAPS).items()}
        self.task_api = RateLimit(task_api_rps)
        self.call_timeout, self.backoff_base, self.backoff_cap, self.sleep = call_timeout, backoff_base, backoff_cap, sleep
        self._stopped = threading.Event()
        self.stop_reason = ""

    # --- run limits ---------------------------------------------------------------------------------------------
    def stop(self, reason: str) -> None:
        if not self._stopped.is_set():
            self.stop_reason = reason
            self._stopped.set()
            for s in self.slots.values():  # waiters re-check and leave at once
                with s._cond:
                    s._cond.notify_all()

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    def fallback_reason(self) -> str | None:
        """Why every open task must go to FALLBACK_SUBMIT now, or None."""
        if self.stopped:
            return self.stop_reason
        if self.deadlines.hard_passed():
            return "hard deadline"
        if self.tracker.total >= self.budget.hard_usd:
            return f"budget ${self.tracker.total:.2f} >= ${self.budget.hard_usd:.2f}"
        return None

    def loop_stop_reason(self) -> str | None:
        """Why no new loop step may start (open tasks compose instead), or None."""
        if r := self.fallback_reason():
            return r
        if self.deadlines.soft_passed():
            return "soft deadline"
        if self.tracker.total >= self.budget.soft_usd:
            return f"budget ${self.tracker.total:.2f} >= ${self.budget.soft_usd:.2f}"
        return None

    def hard_deadline(self) -> float:
        return self.deadlines.start + self.deadlines.hard_s

    # --- calls --------------------------------------------------------------------------------------------------
    def call(self, endpoint: str, fn: Callable[[float], T], *, deadline: float | None = None,
             shrink: Callable[[], bool] | None = None, log: Log | None = None) -> T:
        """Run `fn(timeout)` under `endpoint`'s cap with the retry policy above. `fn` raises `llm.LLMError` on
        failure; `shrink()` makes the caller's next request smaller and returns False when it can't."""
        slots = self.slots[endpoint]
        deadline = min(deadline or self.hard_deadline(), self.hard_deadline())
        attempt = n400 = 0
        while True:
            if self.stopped:
                raise BudgetExhausted(self.stop_reason)
            if time.time() >= deadline:
                raise DeadlineExceeded(f"{endpoint}: deadline passed")
            slots.acquire(deadline)
            try:
                if self.stopped:  # stopped while this call waited for a slot
                    raise BudgetExhausted(self.stop_reason)
                return fn(max(0.1, min(self.call_timeout, deadline - time.time())))
            except llm.LLMError as e:
                err = e
                if e.status == 403:  # before the slot frees, so no waiter starts a call that is bound to fail
                    self.stop("403 from the model endpoint (key limit)")
            finally:
                slots.release()
            slots.error(err.status)
            if log:
                log({"event": "model_error", "endpoint": endpoint, "status": err.status, "provider": err.provider,
                     "retry_after": err.retry_after, "attempt": attempt, "error": str(err)[:300],
                     "body": err.body[:1000]})
            attempt += 1
            if err.status == 403:
                self.stop("403 from the model endpoint (key limit)")
                raise BudgetExhausted(self.stop_reason) from err
            if err.status == 400:
                n400 += 1
                if n400 == 1 or (shrink is not None and shrink()):
                    continue
                raise NeedsShrink(str(err)) from err
            if not _retryable(err):
                raise err
            wait = max(err.retry_after, random.uniform(0, min(self.backoff_cap, self.backoff_base * 2 ** (attempt - 1))))
            if time.time() + wait >= deadline:
                raise DeadlineExceeded(f"{endpoint}: backing off {wait:.1f}s would pass the deadline") from err
            self.sleep(wait)

    def chat(self, messages: list[dict], *, deadline: float | None = None, shrink: Callable[[], bool] | None = None,
             log: Log | None = None, **kw: Any) -> dict:
        """`llm.chat` on the main model (Gemma), paced. `messages` may be changed in place by `shrink`."""
        return self.call("gemma", lambda t: llm.chat(messages, tracker=self.tracker, retries=0, timeout=t, **kw),
                         deadline=deadline, shrink=shrink, log=log)

    def decide(self, state: Any, questions: dict[str, dict], *, deadline: float | None = None,
               log: Log | None = None, **kw: Any) -> dict:
        return self.call("jev", lambda t: llm.decide(state, questions, tracker=self.tracker, retries=0, timeout=t, **kw),
                         deadline=deadline, log=log)

    def embed(self, texts: list[str], *, deadline: float | None = None, log: Log | None = None,
              **kw: Any) -> list[list[float]]:
        return self.call("embed", lambda t: llm.embed(texts, tracker=self.tracker, retries=0, timeout=t, **kw),
                         deadline=deadline, log=log)
