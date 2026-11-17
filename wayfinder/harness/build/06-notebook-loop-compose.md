# 06: Notebook loop and compose

**What to build:** Tasks without a solver are answered end to end: the notebook loop (SPEC §6: fixed-size prompts ≤ ~6k tokens, `search`/`lookup`/`open`/server read-only tools/`note`/`done`, repeat-call cache, step caps 6/8/10, data markers and untrusted tags), then compose writes structured JSON against the instruction's schema with citations by handle, then code normalisation (UTC `…Z` timestamps, parsed JSON, numbers, sorting, keys) and the citation cap, then submit; the fallback ladder applies on any failure.

**Blocked by:** 02, 03, 04

**Status:** ready-for-agent

- [ ] First real Score on development runs (≥ 2 runs, sequential), reported with cost
- [ ] No prompt above the cap; no locator typed by the model
- [ ] Zero answer-format losses in the grader feedback
- [ ] Commit + JOURNAL entry with the run ids and Score

Spec: [SPEC.md](../SPEC.md)
