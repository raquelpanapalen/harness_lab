# 10: Solver framework and wave 1

**What to build:** Solvers plug into SOLVE: two-part recognition (intake family + preconditions), answer + evidence handles + confidence, partial facts seeding the loop on failure. Wave 1: compromised agents, patient zero, timeline. A solver is enabled only if it beats the loop by Score on its family's development tasks.

**Blocked by:** 05, 06, 09

**Status:** done

- [x] Per-family Score: solver vs. loop on development runs, recorded
- [x] Enabled solvers raise the run Score; disabled ones are listed with the reason
- [x] A forced precondition failure falls back to the loop and still submits

Spec: [SPEC.md](../SPEC.md)

## Result

Framework: `harness/solvers/__init__.py` (`Solution`, `REGISTRY`, `ENABLED`, `register`, `Precondition`, `run`). SOLVE in
`machine.py` calls `solvers.run(ctx, rec)`: confident → `rec.draft` → CHECK; otherwise notes (with handles) → LOOP.
A `Precondition` or any other exception goes to the loop too. Wave 1 in `harness/solvers/core.py`.

**Per family, dev runs** (`tests/check_solvers.py`, 39 runs, graded by `replay.grade`; loop = rp008/rp010 family mean):

| family | tasks | confident | Score (confident) | loop Score | enabled |
|---|---|---|---|---|---|
| compromised | 45 | 45 | 1.000 | 0.531 | yes |
| patient_zero | 46 | 46 | 1.000 | 0.281 | yes |
| timeline | 117 | 108 → 117* | 0.999 | 0.302 | yes |

\* The 9 unconfident timeline tasks needed approvals that live in the on-call notes. The facts now read them (b036f04),
and 3 runs were re-checked.

**Run Score** (replay, same seed 125823749 = run_NLzokmjwLUfD): rp008 0.284 / rp010 0.255 (loop only) →
**rp001 0.368** (A 0.375/0.339 → 0.468, cost $0.226/$0.232 → $0.189). The run's 9 wave-1 tasks: patient zero
0.60/0.16 → 1.00, compromised 0.81/0.32 → 1.00, timeline 0.30/0.35 → 1.00, with 0 tool calls each.

No family is disabled. Forced precondition failure: `tests/test_solvers.py::test_forced_precondition_failure_still_submits`
(through `machine.run_task`, the solver raises `Precondition`, the loop hands over to FALLBACK_SUBMIT, the task is
submitted).
