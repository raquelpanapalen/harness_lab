"""Fault-injection checks of the lab harness pacer (build ticket 03): python tests/test_lab_pacing.py

Fake endpoints return 429, 400, 5xx, 403 and slow responses; the pacer must retry, shrink, stop and cap as SPEC §8
says, and a run whose key hits its limit midway must still submit every task.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import openai

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import harness  # noqa: E402
from arena import llm  # noqa: E402
from arena.client import Run, Task  # noqa: E402
from harness.machine import HANDLERS, Deadlines  # noqa: E402
from harness.pacing import (CAPS, TASK_API_RPS, Budget, BudgetExhausted, DeadlineExceeded, NeedsShrink,  # noqa: E402
                            Pacer, Slots)
from harness.state import State, TaskRecord  # noqa: E402
from test_lab_machine import FakeClient  # noqa: E402

E = llm.LLMError


def pacer(**kw) -> Pacer:
    kw.setdefault("backoff_base", 0.01)
    kw.setdefault("backoff_cap", 0.05)
    return Pacer(llm.CostTracker(), kw.pop("deadlines", Deadlines()), **kw)


def endpoint(*script):
    """A fake model endpoint: each call takes the next item; an exception is raised, anything else returned."""
    calls = []

    def fn(timeout):
        calls.append(timeout)
        item = script[min(len(calls), len(script)) - 1]
        if isinstance(item, Exception):
            raise item
        return item
    return fn, calls


def test_defaults_follow_spec() -> None:
    d, b = Deadlines(), Budget()
    assert (d.soft_s, d.hard_s, b.soft_usd, b.hard_usd) == (24 * 60, 27 * 60, 0.50, 1.00)
    assert CAPS == {"gemma": (6, True), "jev": (10, False), "embed": (2, False)} and TASK_API_RPS == 15
    p = pacer()
    assert p.slots["gemma"].limit == 6 and p.fallback_reason() is None and p.loop_stop_reason() is None


def test_429_backs_off_halves_gemma_and_honours_retry_after() -> None:
    sleeps: list[float] = []
    p, logs = pacer(sleep=sleeps.append), []
    fn, calls = endpoint(E("rate", status=429, retry_after=2.5, provider="DeepInfra", body='{"error":"rate"}'), "ok")
    assert p.call("gemma", fn, log=logs.append) == "ok" and len(calls) == 2
    assert sleeps == [2.5], sleeps                                   # Retry-After wins over the (small) backoff
    assert p.slots["gemma"].limit == 3
    assert logs[0]["status"] == 429 and logs[0]["provider"] == "DeepInfra" and "rate" in logs[0]["body"]
    fn, _ = endpoint(E("rate", status=429), "ok")
    p.call("jev", fn)
    assert p.slots["jev"].limit == 10                                 # only Gemma's limit adapts


def test_adaptive_limit_recovers_one_per_error_free_minute() -> None:
    now = [0.0]
    s = Slots(6, adaptive=True, clock=lambda: now[0])
    s.error(429), s.error(429), s.error(429)
    assert s.limit == 1
    now[0] += 59
    s.acquire(time.time() + 1), s.release()
    assert s.limit == 1
    now[0] += 1
    s.acquire(time.time() + 1), s.release()
    assert s.limit == 2
    s.error(500)                                                      # any error restarts the minute
    now[0] += 30
    s.acquire(time.time() + 1), s.release()
    assert s.limit == 2
    now[0] += 30 + 10 * 60
    s.acquire(time.time() + 1), s.release()
    assert s.limit == 6                                               # never above the cap


def test_5xx_408_timeouts_retry_until_deadline() -> None:
    for err in (E("bad gateway", status=502), E("timeout", status=408), E("read timeout"), E("No choices")):
        fn, calls = endpoint(err, err, "ok")
        assert pacer().call("jev", fn) == "ok" and len(calls) == 3
    p = pacer()
    fn, calls = endpoint(E("down", status=503))
    t0 = time.time()
    try:
        p.call("gemma", fn, deadline=time.time() + 0.4)
        raise AssertionError("must give up at the deadline")
    except DeadlineExceeded:
        pass
    assert time.time() - t0 < 0.6 and len(calls) > 3, (time.time() - t0, len(calls))
    fn, calls = endpoint(E("in-flight budget", status=402, retry_after=0.01), "ok")
    assert pacer().call("gemma", fn) == "ok"                           # OpenRouter's 402 + Retry-After
    fn, calls = endpoint(E("payment", status=402))
    try:
        pacer().call("gemma", fn)
        raise AssertionError("a 402 without Retry-After is final")
    except E:
        assert len(calls) == 1


def test_slow_endpoint_gets_bounded_timeouts() -> None:
    p = pacer(call_timeout=0.05)

    def slow(timeout):
        time.sleep(min(timeout, 1.0))
        raise E("timed out")                                          # what the SDK raises when the timeout hits
    t0 = time.time()
    try:
        p.call("gemma", slow, deadline=time.time() + 0.3)
        raise AssertionError("a slow endpoint must hit the deadline")
    except DeadlineExceeded:
        pass
    assert time.time() - t0 < 0.5
    fn, calls = endpoint("ok")
    p.call("gemma", fn, deadline=time.time() + 0.02)
    assert calls[0] <= 0.1, calls                                     # never waits past the deadline


def test_400_retries_once_then_shrinks() -> None:
    fn, calls = endpoint(E("bad", status=400), "ok")
    assert pacer().call("gemma", fn) == "ok" and len(calls) == 2
    shrinks = []
    fn, calls = endpoint(E("too long", status=400), E("too long", status=400), "ok")
    assert pacer().call("gemma", fn, shrink=lambda: shrinks.append(1) or True) == "ok"
    assert len(calls) == 3 and len(shrinks) == 1
    fn, calls = endpoint(E("too long", status=400))
    for shrink in (None, lambda: False):
        try:
            pacer().call("gemma", fn, shrink=shrink)
            raise AssertionError("a second 400 without shrinking asks the caller to shrink")
        except NeedsShrink:
            pass
    logs = []
    fn, _ = endpoint(E("bad", status=400, provider="Novita", body="context length"), "ok")
    pacer().call("gemma", fn, log=logs.append)
    assert logs[0]["provider"] == "Novita" and logs[0]["body"] == "context length"


def test_403_stops_every_model_call() -> None:
    p = pacer()
    fn, calls = endpoint(E("limit", status=403))
    try:
        p.call("gemma", fn)
        raise AssertionError
    except BudgetExhausted:
        pass
    assert p.stopped and "403" in (p.fallback_reason() or "")
    fn2, calls2 = endpoint("ok")
    for ep in ("gemma", "jev", "embed"):
        try:
            p.call(ep, fn2)
            raise AssertionError
        except BudgetExhausted:
            pass
    assert len(calls) == 1 and not calls2


def test_concurrency_caps() -> None:
    p = pacer()
    for ep, cap in (("gemma", 6), ("jev", 10), ("embed", 2)):
        def slow(timeout):
            time.sleep(0.05)
            return "ok"
        ts = [threading.Thread(target=p.call, args=(ep, slow)) for _ in range(25)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert p.slots[ep].peak == cap, (ep, p.slots[ep].peak)


def test_task_api_rate() -> None:
    p = pacer(task_api_rps=50)
    t0 = time.time()
    for _ in range(100):                                              # 50 burst + 50 at 50/s ≈ 1 s
        p.task_api.acquire()
    assert 0.8 < time.time() - t0 < 1.5, time.time() - t0


def test_budget_thresholds_and_soft_deadline() -> None:
    p = pacer(budget=Budget(soft_usd=0.1, hard_usd=0.2))
    p.tracker.total = 0.15
    assert p.loop_stop_reason() and not p.fallback_reason()
    p.tracker.total = 0.25
    assert "budget" in (p.fallback_reason() or "")
    p = pacer(deadlines=Deadlines(start=time.time() - 10, soft_s=5, hard_s=60))
    assert p.loop_stop_reason() == "soft deadline" and not p.fallback_reason()


def _loop_handlers(model_call):
    """LOOP makes one paced model call, then COMPOSE drafts an answer; CHECK passes it."""
    def loop(c, rec):
        model_call(c, rec)
        return State.COMPOSE

    def compose(c, rec):
        rec.draft = {"outcome": "OK", "answer": ["x"], "sources": []}
        return State.CHECK
    return {**HANDLERS, State.LOOP: loop, State.COMPOSE: compose}


def test_run_with_403_midway_submits_every_task() -> None:
    n = {"calls": 0, "after_stop": 0}
    lock = threading.Lock()

    def model(timeout, p):
        with lock:
            n["calls"] += 1
            n["after_stop"] += p.stopped
            k = n["calls"]
        time.sleep(0.01)
        if k > 8:
            raise E("Key limit exceeded", status=403, body='{"error":{"code":403}}')
        return "ok"

    handlers = _loop_handlers(lambda c, rec: c.pacer.call("gemma", lambda t: model(t, c.pacer), log=lambda e: c.trace(rec.task_id, e)))
    with tempfile.TemporaryDirectory() as d:
        client = FakeClient()
        tasks = [Task(task_id=f"t{i:02}", instruction="List them as a JSON array.", level=1) for i in range(30)]
        res = harness.run(client, Run(run_id="r1", kind="dev"), tasks, llm.CostTracker(), Path(d), handlers=handlers)
        assert sorted(s[0] for s in client.submits) == [t.task_id for t in tasks], client.submits
        assert all(TaskRecord.load(Path(d) / "state" / f"{t.task_id}.json").state is State.DONE for t in tasks)
        assert len(res) == 30
        answered = sum(s[2] == ["x"] for s in client.submits)
        # Tasks that composed before the 403 kept their answers; open ones (even with a model reply in hand) fell back.
        assert 1 <= answered <= 8, answered
        assert n["after_stop"] == 0 and n["calls"] < 30, n            # once the 403 is known, no call starts
        late = [r for t in tasks if (r := TaskRecord.load(Path(d) / "state" / f"{t.task_id}.json"))
                and any("403" in x.get("note", "") for x in r.transitions)]
        assert late, "tasks after the 403 went to FALLBACK_SUBMIT on the stop, without calling the model"


def test_budget_watcher_in_the_machine() -> None:
    called = []
    handlers = _loop_handlers(lambda c, rec: called.append(rec.task_id))
    for spent, expect_answer, expect_loop in ((0.0, ["x"], True), (0.6, ["x"], False), (1.0, [], False)):
        called.clear()
        with tempfile.TemporaryDirectory() as d:
            client, tracker = FakeClient(), llm.CostTracker()
            tracker.total = spent
            harness.run(client, Run(run_id="r1", kind="dev"), [Task(task_id="t01", instruction="a JSON array", level=1)],
                        tracker, Path(d), handlers=handlers)
            assert client.submits[0][2] == expect_answer, (spent, client.submits)
            assert bool(called) == expect_loop, (spent, called)


def test_confident_tasks_are_submitted_before_loop_tasks() -> None:
    def solve(c, rec):
        if rec.task_id.endswith(("0", "2", "4", "6", "8")):
            rec.draft = {"outcome": "OK", "answer": ["solver"], "sources": []}
            return State.COMPOSE
        return State.LOOP

    def loop(c, rec):
        time.sleep(0.01)
        rec.draft = {"outcome": "OK", "answer": ["loop"], "sources": []}
        return State.COMPOSE
    handlers = {**HANDLERS, State.SOLVE: solve, State.LOOP: loop, State.COMPOSE: lambda c, rec: State.CHECK}
    with tempfile.TemporaryDirectory() as d:
        client = FakeClient()
        tasks = [Task(task_id=f"t{i:02}", instruction="a JSON array", level=1) for i in range(20)]
        harness.run(client, Run(run_id="r1", kind="dev"), tasks, llm.CostTracker(), Path(d), handlers=handlers)
        order = [s[2][0] for s in client.submits]
        assert order == ["solver"] * 10 + ["loop"] * 10, order


def test_llm_errors_carry_status_retry_after_provider() -> None:
    body = {"error": {"message": "Provider returned error", "code": 429, "metadata": {"provider_name": "DeepInfra"}}}
    resp = httpx.Response(429, headers={"retry-after": "3"}, json=body, request=httpx.Request("POST", "http://x"))

    def make():
        raise openai.RateLimitError("rate limited", response=resp, body=body)
    try:
        llm._sdk_call(make, retries=0)
        raise AssertionError
    except E as e:
        assert (e.status, e.retry_after, e.provider) == (429, 3.0, "DeepInfra") and "DeepInfra" in e.body, vars(e)

    class R:
        status_code, headers, text = 403, {}, '{"error":{"message":"Key limit exceeded"}}'

        def json(self):
            return {"error": {"message": "Key limit exceeded"}}
    real = llm.httpx.post
    llm.httpx.post = lambda url, **kw: R()
    try:
        llm._jev_post({}, retries=0)
        raise AssertionError
    except E as e:
        assert e.status == 403 and "Key limit" in e.body
    finally:
        llm.httpx.post = real


if __name__ == "__main__":
    import os
    os.environ.setdefault("OPENROUTER_API_KEY", "sk-or-test")  # _jev_post builds headers; no request leaves
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("lab pacing tests ok")
