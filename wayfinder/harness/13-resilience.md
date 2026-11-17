# Provider errors, retries and pacing

Label: `wayfinder:grilling`
Status: closed
Assignee: Raquel (session)
Blocked by: 06-pipeline-architecture.md
Map: [New harness for Harness Arena](map.md)

## Question

Two thirds of the baseline's lost points were provider errors (429 under 8 parallel workers; 400 on prompts above ~8k
tokens) that the kit never retried. How does the harness pace calls inside 30 minutes (workers per model, a shared rate
limiter), retry 429/400/5xx (backoff, shrinking context on 400, logging `e.body` and the provider), checkpoint so a
task resumes rather than restarts, and guarantee a submission before the deadline or the $3 limit (403)?

## Resolution

Decided with the human (grilling, all recommendations accepted):

1. One scheduler runs tasks in parallel with per-endpoint caps: Gemma ≤ 6 in flight (a 429 halves it; +1 per
   error-free minute), Jev ≤ 10, embeddings ≤ 2, Task API a shared 15 requests/s allowance.
2. Retries: 429/5xx/408/timeouts → exponential backoff with jitter, honour `Retry-After`, bounded by the task's deadline
   rather than an attempt count. 400 → retry once as is, then shrink context (halve the tool result, condense the
   notebook) and retry; always log the error body and provider. 403 (budget) → stop all model calls, every open task to
   FALLBACK_SUBMIT.
3. Timeline: setup ≤ 3 min; confident-solver tasks first, then loop tasks; soft deadline 24 min (no new loop steps,
   open tasks to compose); hard deadline 27 min (FALLBACK_SUBMIT everything); finish by 28 min.
4. Task records saved to `runs/<run>/state/<task>.json` after every transition; retries resume from the last state; a
   restarted process picks up the same active run and continues open tasks.
5. Development measurement runs are sequential, never two runs on one key at once.

## Addendum (from Model roles and budget per level)

Run budget watcher: no new loop steps at $0.50; FALLBACK_SUBMIT for open tasks at $1.00 (in addition to the 403 rule).
