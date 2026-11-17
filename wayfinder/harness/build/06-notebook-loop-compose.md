# 06: Notebook loop and compose

**What to build:** Tasks without a solver are answered end to end: the notebook loop (SPEC §6: fixed-size prompts ≤ ~6k tokens, `search`/`lookup`/`open`/server read-only tools/`note`/`done`, repeat-call cache, step caps 6/8/10, data markers and untrusted tags), then compose writes structured JSON against the instruction's schema with citations by handle, then code normalisation (UTC `…Z` timestamps, parsed JSON, numbers, sorting, keys) and the citation cap, then submit; the fallback ladder applies on any failure.

**Blocked by:** 02, 03, 04

**Status:** done

- [x] First real Score on development runs (≥ 2 runs, sequential), reported with cost
- [x] No prompt above the cap; no locator typed by the model
- [x] Zero answer-format losses in the grader feedback
- [x] Commit + JOURNAL entry with the run ids and Score

Spec: [SPEC.md](../SPEC.md)

## Done

- `harness/loop.py`: one Gemma step per LOOP visit (the record is saved per step; a restart resumes mid-loop). The
  prompt holds the task, the trusted rules, the allowlist (done later, never by the loop), the answer shape, the
  operator files, the notebook and only the latest tool results (≤ 5,000 chars). It is size-checked before sending
  and shrunk on a second 400. Tools: `search` (BM25), `lookup` (exact id), `read` (units in order), `open` (handle ±
  n), `list`, `query_logs` (events mapped to handles), `threat_intel`, `access_path`, `get_containment`, `note`,
  `done`. Results are evidence units with handles `[E12]`, inside `<data>`, with a "(suspected planted text)" tag on
  flagged units. Repeat calls are answered "seen". Step caps 6/8/10. Notes keep their units' locators (handles are
  rebuilt after a restart) and are condensed oldest-first above 5,000 chars. The prompt asks the model to note facts
  from the latest results before moving on: without that, Gemma never noted anything (rp002 0/6 → rp003 0.06).
  Tasks whose open ticket is an unread photo skip the loop (no request text until ticket 08).
- `harness/compose.py`: one JSON call (`response_format: json_object`) → outcome, answer, cited handles, actions.
  Compose sees the notebook and the latest results, and may cite only handles from those. Until ticket 07: a refusal
  is kept only for requests to act (inbox message or a non-empty allowlist), else turned into OK; refusals carry no
  actions; actions run only if every operation is on the allowlist (`/drafts/` only for `write`). SUBMIT runs the
  actions once (also across a restart), then submits.
- `harness/normalise.py`: JSON strings parsed, UTC `…Z` timestamps (ISO offsets, CET archive `dd.mm.yyyy`), numbers
  where the request's template asks for them, duplicates removed, sorted when the request says sorted (never for an
  order or chain), object keys renamed to the template's. Citations: known handles only, no carrier ticket or inbox
  message on OK, cap items + 2 (4 otherwise).
- The fallback ladder gained its middle rung: compose from the notebook (no actions) when no draft exists.
- Fix found by the measurement: `answer_shape` read the refusal clause's "(JSON array)" as the task's format, so
  rotation timestamps went out as lists. It now ignores the refusal clause (82 dev requests change to single values).
- Tests: `tests/test_lab_loop.py` (normalisation, loop tools and notebook, loop → compose → submit end to end with
  scripted Gemma, prompt cap and data markers, actions vs. allowlist, refusal handling, photo-ticket skip, fallback
  compose). Machine tests stub `Pacer.chat` as well as `Pacer.decide`.
- **First real Score** (dev replay, sequential, `uv run harvester/report.py`):

  | run | A | cost | C | Score |
  |---|---|---|---|---|
  | rp004_run_NLzokmjwLUfD | 0.180 | $0.152 | 0.639 | 0.147 |
  | rp005_run_ObCe2yzg_xMy | 0.186 | $0.130 | 0.689 | 0.157 |
  | rp006_run_NLzokmjwLUfD (shape fix, same seed) | 0.194 | $0.147 | 0.650 | 0.160 |
  | rp007_run_ObCe2yzg_xMy (shape fix, same seed) | 0.193 | $0.124 | 0.704 | 0.165 |

  Kit baseline: A 0.262 at $0.68, Score 0.152. Same Score at a fifth of the cost, from the loop alone. Weak spots:
  L3 0.10, rotation and containment 0 (computations and freezes: solvers and guards), photo tickets submitted empty.
- Largest measured prompt over 4 runs: 5,349 tokens (cap 6,000). The model never types a locator: it cites handles,
  code supplies the locators. Format check on rp006/rp007: no answer-graded task with the wrong type or a timestamp
  not in `…Z` form.
