# 03: Pacing and resilience

**What to build:** Model and Task API calls go through one scheduler with per-endpoint caps (Gemma ≤ 6 in flight, halved on 429, +1 per error-free minute; Jev ≤ 10; embeddings ≤ 2; Task API 15 req/s). Retries by error type: 429/5xx/408/timeouts back off with jitter, honour Retry-After, bounded by the task deadline; 400 retries once, then the caller is asked to shrink its context; the error body and provider are logged; 403 stops all model calls and sends open tasks to FALLBACK_SUBMIT. Soft deadline 24 min, hard 27 min; budget watcher at $0.50 (no new loop steps) and $1.00 (fallback all). Confident-solver tasks are scheduled before loop tasks.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] Fault-injection tests (fake endpoints returning 429, 400, 5xx, 403, slow responses) show the specified behaviour
- [ ] A run with a simulated 403 midway still submits every task
- [ ] Deadlines and budget thresholds are configurable for tests and default to the spec values

Spec: [SPEC.md](../SPEC.md)
