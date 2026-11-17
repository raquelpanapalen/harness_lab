# Workspace index and retrieval

Label: `wayfinder:grilling`
Status: closed
Assignee: Raquel (session)
Blocked by: 02-task-taxonomy.md, 04-model-capabilities.md
Map: [New harness for Harness Arena](map.md)

## Question

How does the harness find the relevant evidence: download workspace.zip once and parse everything locally, BM25, embeddings, the server's search tool, or a mix? What does the index store (lines, rows, records) so citations can point at exact locators?

## Resolution

Decided with the human (grilling, all recommendations accepted):

1. Acquisition: once per run, fetch the run manifest and download every distinct blob in parallel (≈ 950 blobs,
   ≈ 100 MB). Parse text/csv/json/xlsx/ods/docx/odt eagerly; PDFs and images lazily, when a task needs them.
2. The index's unit is the **evidence unit**: exactly what the grader cites (line for text/md/jsonl, record for
   csv/json, sheet+row for xlsx/ods, paragraph for docx/odt, page for pdf, path for images), carrying path + locator.
3. The model sees evidence units under short handles (`E17`) and cites handles; code turns them into `sources` and can
   cap the count. The model never types a locator.
4. Every csv, xlsx/ods sheet and the audit log is loaded into SQLite, one table per file version (blob hash). Code
   solvers query it; the model gets search plus fixed lookups (agent, token, trace, request id), not raw SQL (a possible
   later experiment).
5. Search v1 = local BM25 over evidence units. Embeddings (prose only, fused with BM25) are added only if measured
   failures show wording mismatch.
6. Read-only tools (`tree`, `list`, `read`, `search`, `query_logs`) are answered from the local index. Server calls
   remain for `threat_intel`, `access_path`, `get_containment`, state-changing tools and `submit`.
7. Isolation: everything keyed by blob hash; a task sees only its manifest's files; derived facts cached per hash, so
   per-task variants (the audit log's planted attacks) never leak between tasks.
8. Evidence units carry an `untrusted` flag set by structural signals (Injection landscape). How it is rendered and
   enforced is decided in Action guard and injection handling.
