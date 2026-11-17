# 03: Pacing and resilience

**What to build:** Model and Task API calls go through one scheduler with per-endpoint caps (Gemma ≤ 6 in flight, halved on 429, +1 per error-free minute; Jev ≤ 10; embeddings ≤ 2; Task API 15 req/s). Retries by error type: 429/5xx/408/timeouts back off with jitter, honour Retry-After, bounded by the task deadline; 400 retries once, then the caller is asked to shrink its context; the error body and provider are logged; 403 stops all model calls and sends open tasks to FALLBACK_SUBMIT. Soft deadline 24 min, hard 27 min; budget watcher at $0.50 (no new loop steps) and $1.00 (fallback all). Confident-solver tasks are scheduled before loop tasks.

**Blocked by:** 02

**Status:** done

- [x] Fault-injection tests (fake endpoints returning 429, 400, 5xx, 403, slow responses) show the specified behaviour
- [x] A run with a simulated 403 midway still submits every task
- [x] Deadlines and budget thresholds are configurable for tests and default to the spec values

Spec: [SPEC.md](../SPEC.md)

## Done

- `harness/pacing.py`: `Pacer` with per-endpoint `Slots` (Gemma 6, adaptive: a 429 halves it, each error-free minute
  adds 1 back up to the cap; Jev 10; embeddings 2) and a shared token-bucket `RateLimit` for the Task API (15 req/s,
  hooked in via the new `ArenaClient.throttle`). `Pacer.call(endpoint, fn(timeout))` retries 429/5xx/408/timeouts/
  network errors/empty responses (and 402 + Retry-After) with full-jitter exponential backoff, at least Retry-After,
  bounded by the deadline (`DeadlineExceeded`); 400 retried once, then `shrink()` or `NeedsShrink`; 403 stops every
  model call (`BudgetExhausted`, set before the slot is freed so no waiter starts a doomed call). Every failure is
  logged with status, provider, Retry-After and error body. Each attempt's timeout is at most 90 s and never past the
  deadline. `Pacer.chat/decide/embed` wrap `arena/llm.py` with the SDK's retries off.
- `arena/llm.py`: `LLMError` carries `status`, `retry_after`, `provider` (OpenRouter's `error.metadata.provider_name`)
  and `body`; `chat`/`decide`/`embed` take `retries=` and `timeout=` (defaults keep the baseline's behaviour).
- Run limits in the machine: before each handler, a 403, the hard deadline (27 min) or the hard budget ($1.00) send the
  task to FALLBACK_SUBMIT; the soft deadline (24 min) or soft budget ($0.50) turn LOOP into COMPOSE. The loop handler
  (ticket 06) must also check `ctx.pacer.loop_stop_reason()` before each step. All thresholds are arguments of
  `Deadlines`, `Budget` and `Pacer`.
- `harness.run`: phase 1 drives every task up to LOOP (so solver-confident tasks submit first), phase 2 runs the parked
  loop tasks. 16 task threads; the pacer caps the calls themselves.
- Tests: `tests/test_lab_pacing.py` (fake endpoints with 429, 400, 5xx, 408, timeouts, empty responses, 402, 403, slow
  responses; caps reached and never exceeded; adaptive limit; Task API rate; budget and soft-deadline thresholds;
  confident-before-loop order; `LLMError` fields from real SDK/httpx error shapes; a 30-task run with a 403 midway
  submits 30/30 and no call starts once the 403 is known). Stable over 5 repeats. Machine and offline tests still pass.
- Replay: rp007_run_NT6kzGi5K2iQ, a fake model returning 403 after 15 calls: 48/48 submitted (21 calls: 15 ok + 6
  already in flight). rp008_run_OA72gAU9T7pb, `main.py --harness lab`: 48/48 submitted at $0.
- Not changed: Task API retries stay the client's own (3 retries on 429/5xx, Retry-After honoured) plus the submit
  retry in the machine; they are now rate-limited but not deadline-bounded.
