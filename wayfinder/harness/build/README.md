# Build tickets for the new harness

From [SPEC.md](../SPEC.md). Frontier: tickets whose blockers are all done. Measure each on development runs, sequentially; judge by Score.

| # | Ticket | Blocked by |
|---|---|---|
| 01 | [Prefactor: selectable harness, shared parsers](01-prefactor-selectable-harness.md) | None |
| 02 | [Tracer: state machine that always submits](02-tracer-state-machine.md) | 01 |
| 03 | [Pacing and resilience](03-pacing-resilience.md) | 02 |
| 04 | [Workspace index](04-workspace-index.md) | 01 |
| 05 | [Intake](05-intake.md) | 02 |
| 06 | [Notebook loop and compose](06-notebook-loop-compose.md) | 02, 03, 04 |
| 07 | [Guards and repair](07-guards-repair.md) | 05, 06 |
| 08 | [Images per need](08-images-per-need.md) | 04, 06 |
| 09 | [Fact layer and the instruction judgment](09-fact-layer.md) | 04 |
| 10 | [Solver framework and wave 1](10-solver-wave-1.md) | 05, 06, 09 |
| 11 | [Solver wave 2](11-solver-wave-2.md) | 10 |
| 12 | [Solver wave 3: inbox routing](12-solver-wave-3.md) | 07, 10 |
| 13 | [Solver wave 4](13-solver-wave-4.md) | 07, 08, 10 |
| 14 | [Wave 5 helpers and code-only cut-offs](14-wave-5-helpers.md) | 06, 09 |
| 15 | [Held-out milestone](15-held-out-milestone.md) | 03, 11, 12, 13, 14 |
| 16 | [Wrap-up: clean code, tidy repo, docs, presentation, article](16-wrap-up-docs-presentation.md) | 15 |

## Module renames (ticket 16, 2026-11-21)

The tickets above name modules as they were built. Ticket 16 renamed them for what they solve:

| Built as | Now |
|---|---|
| `harness/solvers/core.py` (wave 1) | `harness/solvers/incident.py` |
| `harness/solvers/wave2.py` | `harness/solvers/access.py` |
| `harness/wave4.py` | `harness/solvers/response.py` |
| `harness/solvers/wave4.py` | `harness/solvers/registry_response.py` |
| `harness/inbox.py` | `harness/solvers/inbox.py` |
| `harness/solvers/wave5.py` | `harness/solvers/judged.py` |
| `harness/facts/world.py` | `harness/facts/runbook.py` |
| `harness/facts/judge.py` | `harness/facts/instruction_judge.py` |
| `harness/tools.py` | `harness/tool_router.py` |
| `harvester/eval_wave4.py` | `harvester/evals/eval_response.py` |
| `tests/test_wave2.py`, `test_wave5.py`, `test_lab_wave4.py` | `tests/test_access.py`, `test_judged.py`, `test_lab_response.py` |
