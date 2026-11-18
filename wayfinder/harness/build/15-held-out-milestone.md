# 15: Held-out milestone

**What to build:** The harness is measured on the 5 held-out runs (sequential runs) and compared with the leaderboard: A, cost, C and Score against AIT 0.750 and Donaufriends 0.524. The first experiments from SPEC §9 start from this baseline.

**Blocked by:** 03, 11, 12, 13, 14

**Status:** done

- [x] Held-out Score reported per run and as a mean, with cost
- [x] JOURNAL entry with run ids; README updated with the result
- [x] If the new harness beats the baseline, it becomes the default

Spec: [SPEC.md](../SPEC.md)

## Done

Code 128514e (waves 1, 2, 4, 5, inbox, guards; no tuning from the held-out runs). A second replay server ran with
`--only-runs 5` on :3997, with 5 sequential `main.py --harness lab --fresh` runs. Only aggregate scores were read,
never held-out traces or task contents.

| held-out run | A | cost | C | Score |
|---|---|---|---|---|
| rp001_run_CrCkPblWSN-Q | 0.907 | $0.0498 | 0.995 | 0.905 |
| rp002_run_bV0bxqQTiZRa | 0.948 | $0.0196 | 1.000 | 0.948 |
| rp003_run_aF3iOYCGwFiX | 0.912 | $0.0257 | 1.000 | 0.912 |
| rp004_run_Wxvjw3WImrpJ | 0.943 | $0.0213 | 1.000 | 0.943 |
| rp005_run_r1mftr6lxlr3 | 0.935 | $0.0281 | 1.000 | 0.935 |
| **mean** | **0.929** | **$0.029** | **0.999** | **0.929** |

- 240/240 tasks submitted. Against the leaderboard: AIT 0.750, Donaufriends 0.524. Against the kit baseline:
  0.152 at $0.68. The held-out Score equals the dev Score (0.929), so nothing was overfitted to dev.
- Costs are each run's own tally (`main.py` "local cost tally"). `report.py` first showed $0.127 and $1.26 for two
  runs: the restarted replay server reused run ids of the earlier kit-baseline held-out runs, and the lab harness
  appended to their trace files. `main.py` now moves an earlier run's directory with the same id aside when a run
  starts fresh.
- The lab harness is now the default (`main.py --harness lab`); README updated with the result.
- Also: `normalise.py` falls back to a fixed CET (UTC+1) if the container has no time-zone database.
