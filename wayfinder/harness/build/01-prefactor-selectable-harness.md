# 01: Prefactor: selectable harness, shared parsers

**What to build:** The run entry point can run either the kit baseline or the new harness (a flag, baseline the default until the new one wins), and the file parsers (pdf, docx, xlsx, ods, odt) live in a shared module both use. The baseline behaves exactly as before.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] Choosing the baseline produces the same behaviour and trace format as today (one-task smoke run on the replay)
- [x] Choosing the new harness reaches a stub that starts the run and finishes it cleanly
- [x] Parsers are shared, not duplicated; the baseline uses them
- [x] Commit + JOURNAL entry

Spec: [SPEC.md](../SPEC.md)

## Done

- `main.py --harness baseline|lab` (baseline default); the baseline's per-task pool moved into `run_baseline`. The lab
  entry point is `harness.run(...)`, a stub that submits nothing.
- Parsers moved to `arena/parse.py` (`extract_text` + per-format functions); the baseline's `download_file` uses them.
  Parity: identical output to the old functions on 46 real workspace files (12 pdf, 12 docx, 12 xlsx, 4 ods, 6 odt).
- Smoke runs on the replay: lab stub rp003_run_NLzokmjwLUfD (48 tasks, 0 submitted, $0, finished cleanly); baseline
  rp004_run_ObCe2yzg_xMy, 1 task, score 1.00, $0.011 (that task made no `download_file` call). The baseline's
  `download_file` was exercised directly on real files: pdf, docx, xlsx, ods and odt parse, jpg points to `view_image`.
- Offline tests: test_offline, test_cache, test_runs pass. test_submit_retry fails before and after this change
  (pre-existing in the kit; not touched).
