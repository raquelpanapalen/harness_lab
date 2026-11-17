# 02: Tracer: state machine that always submits

**What to build:** Every task of a run goes through the per-task state machine (SPEC §2) and ends in FALLBACK_SUBMIT with a right-shaped empty answer (`[]`, `{}` or `null` by the instruction's format) and outcome OK. The task record is saved after every transition; a restarted process picks up the same active run and continues open tasks. The deadline watcher forces open tasks to FALLBACK_SUBMIT.

**Blocked by:** 01

**Status:** done

- [x] A full development run on the replay submits 48/48 tasks at $0
- [x] `report.py` reports A, cost, C and Score for it
- [x] Killing the process mid-run and restarting resumes the same run without resubmitting
- [x] Every state has a path to FALLBACK_SUBMIT (tested by forcing errors in each state)

Spec: [SPEC.md](../SPEC.md)

## Done

- `harness/state.py`: `State` enum, `TaskRecord` saved atomically to `runs/<run>/state/<task>.json` after every
  transition; answer shape from the instruction (list / object / number / unknown) and the right-shaped empty
  submission.
- `harness/machine.py`: one handler per state, `run_task` driver. Any exception or the hard deadline takes the
  FALLBACK_SUBMIT edge; FALLBACK_SUBMIT always ends in DONE (submits the best draft, else the empty answer; computes the
  shape itself if INTAKE failed); transport errors on submit retried, 409 counts as submitted; a handler that never
  advances is cut off. LOOP, COMPOSE and REPAIR are not built yet and go straight to FALLBACK_SUBMIT. Deadlines (soft
  24 min, hard 27 min) count from the run's first start (`state/_run.json`).
- Run-level resume: `runs/ACTIVE.json` names the active run; a restarted `main.py --harness lab` continues it
  (`--fresh` starts a new one); records resume mid-task.
- Traces: `runs/<run>/<task>.jsonl` with `cost_total`, so `report.py` reads the spend.
- Tests: `tests/test_lab_machine.py` (failure injected into every state, failing fallback submit, 503 retry, 409,
  hard deadline, non-advancing handler, mid-task resume, answer shapes). Existing offline tests still pass.
- Replay: rp005_run_zJJcgkSZFc-3, 48/48 submitted, $0, A 0.000, C 1.000, Score 0.000. Crash test: a process solved 10
  tasks of rp006_run_Yb6jXZ908-nH and died; the next `main.py --harness lab` resumed it, skipped the 10 and submitted
  the other 38 (48/48).
