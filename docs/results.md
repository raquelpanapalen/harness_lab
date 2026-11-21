# Results

Every milestone measurement in one table. Details, run ids and per-run numbers: `JOURNAL.md` (the entry named in the
last column) and the version reports in [`reports/`](reports/).

## How a measurement is scored

- **Score** = `A · (0.5 + 0.5 · C)`, the event leaderboard's metric. **A** is the mean task score over all tasks of
  the run (unsubmitted = 0). **C** is the cost factor on a log scale, fixed to the event's range: 1 at ≤ $0.049 per run,
  0 at ≥ $1.135, `C = ln(1.135 / cost) / ln(1.135 / 0.049)` in between. `harvester/report.py` prints all four.
- Per task: `score = constraints_ok ? answer · (0.7 + 0.3 · sources) : 0` (the grader in `harvester/replay/grade.py`,
  calibrated against the real one: [`development.md`](development.md)).
- Cost is the run's OpenRouter spend (the local tally of `usage.cost`, matched to the key's usage on live runs).

## Milestones

| Measurement | Code | Runs | A | Cost / run | C | Score | Journal |
|---|---|---|---|---|---|---|---|
| Kit baseline, dev | `f160529` | 2 dev | 0.262 | $0.68 | 0.16 | 0.152 | 19:45, research 01 |
| Kit baseline, held-out replay | `f160529` | 3 held-out | 0.347 | $1.26–1.55 | 0 | ≈ 0.17 | 19:45 |
| Notebook loop only (no solvers) | `28c22e7` | 2 dev | 0.194 | $0.136 | 0.68 | 0.163 | 15:30 Build 06 |
| Solvers waves 1–4 + inbox | `128514e` | 2 dev | 0.929 | $0.023 | 1.00 | 0.929 | 14:30 Builds 12/13 |
| **Held-out milestone** | `08cd2ab` | 5 held-out | 0.929 | $0.029 | 1.00 | 0.929 | 15:20 Build 15 |
| Live post-event dev run `run_QVhW_pCITnL1` | `08cd2ab` | 1 live | 0.949 | $0.026 | 1.00 | 0.949 | 16:10 |
| **Citation work, held-out** (`v1`) | `6b09b7d` | 5 held-out | 0.966 | $0.029 | 1.00 | **0.965** | 16:35 |
| Live post-event dev run `run_K7kqkshU13Ff` | `2b31c3f` | 1 live | 0.996 | $0.018 | 1.00 | **0.996** | 16:35 |
| Held-out reskinned to `port` (new vocabulary) | `6b09b7d` harness | 5 held-out | 0.965 | $0.031 | 0.99 | 0.962 | 18:35 |
| Held-out reskinned to `layout` (renamed files, ids) | `6b09b7d` harness | 1 of 5 | 0.258 | $0.17 | 0.60 | ≈ 0.21 | 18:35 |
| After the ticket 16 restructure, rp001 | `c7f8f59` | 1 held-out | 0.938 | $0.052 | 0.98 | 0.931 | 19:30 |

Event leaderboard for comparison: AIT 0.750 (A 0.751 at $0.049), Donaufriends 0.524.

The ticket 16 row checks that the cleanup changed nothing: the same run scored 0.941 at $0.051 before it, and only one
task (a citation that depends on reading a photo) differs.

## Hub held-out at `v1`, by level and kind

| | Tasks | Score | Answer | Sources |
|---|---|---|---|---|
| Level 1 | 80 | 0.986 | 1.000 | 0.95 |
| Level 2 | 80 | 0.990 | 0.998 | 0.97 |
| Level 3 | 80 | 0.923 | 0.950 | 0.87 |
| Refusals (DENIED / CLARIFY / UNSUPPORTED) | 9 | 1.000 | 1.000 | 1.00 |
| Containment and other actions | 21 | 0.98–1.00 | | |
| Notifications | 7 | 0.886 | 0.920 | 0.83 |

All 240 tasks submitted; no task broke a rule (constraints 1.00 everywhere).

## Held-out discipline

- The 5 most recent harvested runs (rp001–rp005: `run_CrCkPblWSN-Q`, `run_bV0bxqQTiZRa`, `run_aF3iOYCGwFiX`,
  `run_Wxvjw3WImrpJ`, `run_r1mftr6lxlr3`) were never used to build or tune the harness: development used the other
  39 (`--exclude-runs 5`), and held-out measurements read aggregates only until the build was finished.
- The milestone's held-out Score equalled the dev Score (0.929 vs 0.929), so nothing was overfitted to dev runs.
- The theme reskins (`harvester/synth/`) were built after the harness, from the held-out runs, to measure overfitting to
  the one theme all harvests share. Reading their per-task results to fix the reskinner ended the held-out status of
  those 5 runs for future work; a new theme should be used for future hold-outs.
