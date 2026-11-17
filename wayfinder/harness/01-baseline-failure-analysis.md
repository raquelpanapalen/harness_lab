# Baseline failure analysis

Label: `wayfinder:research`
Status: closed
Assignee: Raquel (research agent, starts when the runs finish)
Blocked by: (none; evidence = 2 fresh baseline runs on development runs, running now)
Map: [New harness for Harness Arena](map.md)

## Question

Why does the kit baseline lose points? Classify every lost point: never submitted (step limit, exception, budget), rule-check failures (which constraint, what triggered it), wrong outcome, wrong answer format, wrong answer, weak citations. What separates L3 failures from L1/L2? Which single changes would recover the most score? Evidence: baseline traces and graded replay results from 2 fresh baseline runs on development runs (decided: held-out traces stay unread).

## Resolution

Findings: branch `research/baseline-failures`, file `research/baseline-failures.md` (pushed, not merged). Evidence: 2
baseline runs on development runs (rp001_run_ERZIzGIYGnri 0.292, rp002_run_pwT8RGHxv0Bq 0.233; $1.35 total). No model
calls. Baseline scored 25.16/96 (0.262), losing 70.84 points:

| Category | Points | Tasks |
|---|---:|---:|
| Never submitted: provider 429 | 29.00 | 29 |
| Never submitted: provider 400 | 18.00 | 18 |
| Wrong answer content | 12.64 | 22 |
| Wrong answer format (prose, wrong keys, `{id: decision}`) | 3.40 | 7 |
| Weak citations | 2.85 | 23 |
| Wrong outcome | 2.00 | 2 |
| Rule-check failures (2 out-of-scope quarantines with NEEDS_CLARIFICATION, 1 injected framing, 1 rotation in freeze) | 1.95 | 4 |
| Replay-server crash (rotate_token without `at`; fixed) | 1.00 | 1 |

- The kit abandons a task on its first model error: two thirds of lost points are infrastructure.
- 400s depend on context size: 0/828 calls under ~8k tokens failed, ~5 % above (all 18 at ~9–18k tokens); not a hard
  limit (most large calls succeed, 262k advertised). Likely one routed provider intermittently rejecting longer
  prompts; unconfirmed because the kit discards `e.body`. 429s cluster from minute ~10.6 with 8 workers on one key.
- Citations: 61/95 misses cite the wrong file (ticket image, runbook, invented path), 18 path without record, 16 wrong
  locator. Over-citation minor.
- Tasks scoring ≥ 0.7: median 8 steps, ~42k prompt tokens; failures hit the 25-step cap at ~140k. 18/49 submitted tasks
  hit the cap (mean 0.20 vs 0.70). 24 % of read/search calls were exact repeats.
- L3: no cliff once submitted (0.61 / 0.43 / 0.47 by level); loses more to 400s (bigger files). L3 misses: wrong DPA
  customer and unread JPG amendment, abandoned compromised-set derivation, CET archive times, board as second leak
  source.
- Ranked recoverable points: (1) never abandon on model error, long 429 backoff ≈ 14; (2) retry 400 with smaller
  context, keep context < ~8k tokens ≈ 8–9; (3) family code solvers ≈ 6–9 (big-file families scored 0 on 19 tasks);
  (4) enforce answer shape ≈ 3; (5) guard state-changing tools ≈ 2.5; (6) citation policy ≈ 1.5–2. Fixes 1+2 alone:
  ≈ 0.26 → ≈ 0.49.
- Process note: one grep glob printed error lines of a held-out replay directory (error strings and timestamps only);
  excluded, nothing used.
