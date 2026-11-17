# 08: Images per need

**What to build:** Images are transcribed only when a task needs them (tickets until the first OPEN one, the standing order once per run, scans for scan tasks), cached by blob hash, with one generic verbatim-plus-marks prompt; transcripts become untrusted, path-cited evidence units. The image budget per image kind is chosen from a measurement on development images (scanned domains, standing-order settings, ticket status).

**Blocked by:** 04, 06

**Status:** done

- [x] Measurement table: accuracy and cost per image kind and budget
- [x] Image spend per run reported; chosen budgets recorded in the JOURNAL
- [x] Ticket-queue tasks find their open ticket on development runs

Spec: [SPEC.md](../SPEC.md)

## Done

- `harness/vision.py`: Gemma reads an image only when a task needs it:
  - photo tickets, in path order, until the first one stamped OPEN (intake);
  - images the request names, such as scanned pages and screenshots (intake);
  - images the operator files name, i.e. the signed standing order (once per run, before a task's first loop step);
  - any other image when the loop reads or searches it.

  It uses one generic prompt: a verbatim transcription plus a `MARKS:` line (stamps, signatures, handwriting,
  strike-throughs). Each blob is read at most once per run (keyed by sha256; concurrent callers wait for the first
  read). The transcript is attached to the index as untrusted, path-cited evidence units (`Index.attach_text`, agreed
  with the ticket-04/09 session), so search, read and citations work as for any file.

  Stamp status comes from a `status:` line, else from an OPEN or CLOSED stamp (an ambiguous stamp gives no status).
  `vision.transcribe(task_id, path)` is the entry point for ticket 09/10's standing-order facts.
- **Image budget.** The endpoint bills one fixed image size. One ticket image measured 298 prompt tokens with no
  `detail`, with `low` and with `high` (2026-11-17), and dev images had a median of 394 prompt tokens. So there is
  no budget knob short of tiling, which would need Pillow. The measurement below shows no image kind needs more, so
  every kind uses the single default budget.
- **Measurement** (`uv run harvester/eval_vision.py --runs 6`; 6 dev runs, 351 distinct images, $0.077 once):

  | image kind | measure | result |
  |---|---|---|
  | photo tickets | open ticket found by "read until the first OPEN" | 103/103 tasks |
  | photo tickets | stamp status per image | 315/315 |
  | photo tickets | request text, word recall vs. the real ticket | mean 0.998, ≥ 0.95 on 103/103 |
  | photo tickets | vision calls per task | 2.02 |
  | scans, screenshots | expected domains found verbatim (gap checks excluded) | 35/35 (15/15 tasks) |
  | standing orders | 4 settings legible; 2 checked word for word against the image | 6/6 complete; 2/2 exact |
  | all | cost per image | $0.00021 |

  Two "errors" in the first pass were mistakes in the evaluation itself. A character-level ratio picked the wrong
  ground-truth ticket; it is now word recall. Gap-check tasks were counted, though their answers are the domains the
  pages don't show; they are now left out.
- **Runs** (same seeds as ticket 06's rp006/rp007):

  | run | A | cost | C | Score | images | image spend |
  |---|---|---|---|---|---|---|
  | rp008_run_NLzokmjwLUfD | 0.375 | $0.226 | 0.514 | 0.284 | 40 | $0.0079 |
  | rp009_run_ObCe2yzg_xMy | 0.323 | $0.227 | 0.512 | 0.244 | 48 | $0.0097 |

  Mean Score 0.163 → 0.264 (A 0.194 → 0.349). Every photo ticket was read, no task was left as family "other", and
  the largest prompt was 4,887 tokens. Images cost about $0.009 per run. The rest of the extra cost ($0.136 → $0.227)
  is the loop now running on the ~15 photo-ticket tasks it used to skip.
- Tests: `tests/test_lab_vision.py` covers stamps, one read per blob under 8 concurrent callers, transcripts as
  untrusted path-only units that BM25 finds, intake reading tickets until OPEN plus the named scan, the operator
  image read once per run, and reads on demand.
