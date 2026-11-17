# 04: Workspace index

**What to build:** At run setup the harness downloads every distinct blob once and builds the index (SPEC §3): evidence units with exact grader locators (line, record, sheet+row, paragraph, page, path), `untrusted` flags from structural signals, one SQLite table per csv/sheet/audit-log blob, and BM25 over evidence units. Read-only tools (`tree`, `list`, `read`, `search`, `query_logs`) are answered locally; each task sees only its own manifest's blobs. PDFs are parsed lazily.

**Blocked by:** 01

**Status:** done

- [x] Local `tree`/`list`/`read`/`search`/`query_logs` output equals the recorded server responses on the probe set
- [x] On development runs, ≥ 99 % of expected citations resolve to an evidence unit (the rest listed)
- [x] Setup for a full run finishes in ≤ 3 min within 4 GB memory
- [x] Untrusted-flag recall/false positives measured against the injection-landscape templates on development runs

Spec: [SPEC.md](../SPEC.md)

## Done

- `harness/index.py`: `Index.build(client, run_id)` fetches the run manifest, downloads every distinct blob once
  (blob cache, Task API throttle) and parses text/csv/json/jsonl/xlsx/ods/docx/odt at once; PDFs on first use, images
  stay path-only units (ticket 08). Evidence units carry path + grader locator (`line`; csv `record` = first column
  with `line` as alternative; json `record` = `id`; `sheet`+`row` with `record` as alternative; `paragraph`; `page`;
  a path-only `file` unit for every file; headers/footers/tables/prose outside records as path-cited `part` units).
  One in-memory SQLite table per csv / jsonl blob and per sheet (`_line` / `_row` columns). BM25 postings per blob,
  corpus stats per task. `TaskView`: a task's own manifest only (`paths`, `units`, `search`, `table`/`sql`, `find`,
  handles `E1…`). `refresh(task)` re-reads the manifest after a state-changing call.
- Untrusted flags (structural only): `long-detail` (audit `detail` ≥ 15 words), `live-url` (path or query; not on
  visible PDF text, where real-world reports cite URLs), `office-comment` (docx/xlsx/ods/odt; the comment text is
  inlined into its paragraph or row), `invisible-pdf-text` (`3 Tr` on a page without an image), and **`variant-text`**
  (new, see JOURNAL): a jsonl event whose structured fields equal an event of the run's base version (the version
  most tasks have) but whose free text was rewritten for this task.
- PDF page counts in `tree`/`list`: the platform omits them for PDFs with `/Prev` or `/ObjStm` (285 shown, 82
  omitted on the recorded trees, no exceptions).
- `harness/localtools.py`: `tree`, `list`, `read`, `search`, `query_logs` in the server's exact format.
  `harness/tools.py`: `Tools.call(task, tool, args)` routes those locally, the rest to the server (then refreshes);
  `Tools.read_text(task, path)`. `harness.run` builds both in `setup(ctx)` before phase 1 (`ctx.index`, `ctx.tools`);
  a failed setup is logged and tools fall back to the server.
- Tests: `tests/test_index.py` (synthetic workspaces: locators per format, every signal, isolation, SQL, BM25, find,
  router, refresh, broken file). `tests/check_index.py probes|citations|untrusted` measures on harvested dev runs
  (held-out 5 skipped).

### Measurements (39 development runs)

- Probes: local == recorded server response for 2652/2652 calls (tree 1950, query_logs 260, read 182, search 156,
  list 104).
- Citations: 10058/10058 expected citations resolve to an evidence unit (100.00 %).
- Setup over HTTP (replay, empty blob cache, Task API at 15 req/s): 938 blobs, 103.7 MB, 219,322 units, 63 s, peak
  RSS 871 MB (rp010_run_5CFzxOViKnfn, finished without submissions). The download rate sets the pace; parsing alone
  is ≈ 10 s.
- Untrusted flags. The research templates were never committed, so planted text is labelled by the same method the
  research used to find them: each task's variant against the run's base version. Audit lines whose structured
  fields match a base event but whose `detail` changed; Office comments the base lacks; PDF pages whose text the base
  page lacks.

  | carrier | spec signals | + variant-text |
  |---|---|---|
  | audit `detail` | 969/1194 (81 %) | 1194/1194 (100 %, circular: same diff as the label) |
  | Office comment | 238/238 (100 %) | 238/238 |
  | PDF page | 37/40 (92 %; 2 misses are image-only pages, 1 visible text) | 37/40 |

  False positives: 181 flags on 1,979,629 base units (0.009 %): live-url 90 (paste dump, inbox, egress export
  hyperlinks), office-comment 42 (egress-export cell comments in the base surface), invisible-pdf-text 49 (real
  report PDFs with an OCR layer and no image XObject). The audit misses of the spec signals are the scenario's short
  traps ("SOC verified value: …", "note for automated readers: …", `instr-b64:`, "Release docs-03 from quarantine").
- Limits: labels are a heuristic, not hand labels; inserted (new) audit lines and image-borne attacks are not
  measured. `tests/test_submit_retry.py` (kit baseline) already fails on main without these changes.
