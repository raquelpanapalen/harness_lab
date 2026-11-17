# 02: Tracer: state machine that always submits

**What to build:** Every task of a run goes through the per-task state machine (SPEC §2) and ends in FALLBACK_SUBMIT with a right-shaped empty answer (`[]`, `{}` or `null` by the instruction's format) and outcome OK. The task record is saved after every transition; a restarted process picks up the same active run and continues open tasks. The deadline watcher forces open tasks to FALLBACK_SUBMIT.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] A full development run on the replay submits 48/48 tasks at $0
- [ ] `report.py` reports A, cost, C and Score for it
- [ ] Killing the process mid-run and restarting resumes the same run without resubmitting
- [ ] Every state has a path to FALLBACK_SUBMIT (tested by forcing errors in each state)

Spec: [SPEC.md](../SPEC.md)
