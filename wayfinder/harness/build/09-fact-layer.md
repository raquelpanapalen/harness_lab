# 09: Fact layer and the instruction judgment

**What to build:** Per run and per audit-log/board version, code computes the shared facts (SPEC §5): decoders, the 'instruction to agents?' judgment (Jev yes/no, Gemma for the uncertain band, cached by hash), attack traces, compromised set, patient zero, exfiltration transfers, attacker domains, permission graph with delegations, standing-order settings (from the transcript once images exist), roster and contacts. Every scenario assumption is a checked precondition.

**Blocked by:** 04

**Status:** done

- [x] Judgment accuracy vs. the development runs' expected traces, and the Jev/Gemma split, measured and recorded
- [x] Derived facts match what the development runs' expected answers imply for the core families
- [x] No dev values in code (review checklist)

Spec: [SPEC.md](../SPEC.md)

## Done

- `harness/facts/decode.py`: zero-width removal, homoglyph folding (Cyrillic/Greek confusables + NFKC), %-escapes,
  HTML entities, base64 / hex runs decoded and appended (`reveal`); `pdf_page_image` pulls a scanned page's image
  out of a PDF without Pillow (JPEG as is, 8-bit RGB/gray re-wrapped as PNG).
- `harness/facts/judge.py`: "instruction aimed at agents?" on the decoded text, one Jev `noul` per content hash
  (cached for the run), Gemma one-word yes/no for 0.3 < p < 0.7, `jev-uncertain` / `error` when neither settles it.
- `harness/facts/world.py`: `Facts(view, judge, transcripts)`, each fact a `Fact(value, evidence units, ok,
  problems)`; `facts_for` caches per fact-relevant blob hashes. Facts: own domain, now, roster, registry, contacts
  (+ verified domains), change log (table, approved transfers, red-team accounts, flagged accounts), merged gateway
  events (audit + CET archive), traces, quarantined artifacts with readable content (text + attachments + scanned
  PDF pages + data-URI images, each image from a transcript), instruction artifacts, attack traces (incl. children of
  board posts), compromised, patient zero, attacker domains, exposed tokens, board origin, transfer register
  (columns recognised by content: header names change per surface), approvals (change log + on-call handover),
  exfiltration under the standing-order settings, quarantined agents, permissions (registry, change log, agents' own
  grants, minus quarantined), effective scopes, lethal trifecta, standing-order settings.
- Standing order: `parse_standing_order(text, decide)` reads each numbered item by pattern and asks Jev the three
  yes/no parameters; agreement settles a value, a conflict leaves it None (fact not ok). Correct on the 6 transcripts
  of ticket 08 and on every transcript used in the evaluation.
- `harness/facts/runtime.py`: `facts_of(ctx, task_id, need)`: run-wide Judge on the pacer, transcribes exactly the
  images the asked facts need through ticket 08's vision (scanned pages / embedded images extracted first).
- `harness/index.py`: sheet tables take the first full-width row as header (egress exports start with a title row).
- Every scenario assumption is a checked precondition (`Fact.ok` false with the reason): runbook / AGENTS.md
  phrases, quarantine index columns, change-log sections, register columns, scope-class names, missing transcripts,
  uncertain verdicts, unknown statuses, range/count disagreements.
- Tests: `tests/test_facts.py` (synthetic workspace with invented names, fake Jev: every fact, both settings
  readings, conflicts, missing inputs). `tests/check_facts.py` measures on dev runs (held-out skipped).

### Measurements (39 development runs; images transcribed with one Gemma call each, cached)

| fact family | tasks | match | ok | ok but wrong |
|---|---|---|---|---|
| compromised | 45 | 45 | 45 | 0 |
| patient zero | 46 | 46 | 46 | 0 |
| attacker domains (inline candidates) | 12 | 12 | 12 | 0 |
| trifecta, registry only | 26 | 26 | 26 | 0 |
| trifecta, current permissions | 14 | 14 | 14 | 0 |
| board origin | 6 | 6 | 6 | 0 |
| exposed tokens | 7 | 7 | 7 | 0 |
| exfiltration: first successful transfer | 15 | 15 | 15 | 0 |
| exfiltration: records (total / per team) | 35 | 35 | 35 | 0 |

- Judgment: 106 distinct artifacts with a counterfactual label (the expected compromised set comes out only with /
  only without the artifact): accuracy 1.000 (54 instruction-bearing, 52 benign), all decided by Jev (no verdict in
  the uncertain band, so Gemma was never called). Cost of the whole evaluation: $0.016 (196 Jev calls, 88 image
  transcriptions).
- Review checklist (no dev values): `harness/facts/*.py` contains no dev ids, domains, timestamps, counts, file
  names or phrases (grep for scenario names, token/ticket/incident ids, agent-id shapes, dates: no hits).
- Limits: labels and the image stand-in are evaluation tools; in the harness the transcripts come from ticket 08's
  vision. Inserted board posts / MCP rule-1 judgments are wave 5 helpers, not measured here.
