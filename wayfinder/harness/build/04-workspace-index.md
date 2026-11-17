# 04: Workspace index

**What to build:** At run setup the harness downloads every distinct blob once and builds the index (SPEC §3): evidence units with exact grader locators (line, record, sheet+row, paragraph, page, path), `untrusted` flags from structural signals, one SQLite table per csv/sheet/audit-log blob, and BM25 over evidence units. Read-only tools (`tree`, `list`, `read`, `search`, `query_logs`) are answered locally; each task sees only its own manifest's blobs. PDFs are parsed lazily.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] Local `tree`/`list`/`read`/`search`/`query_logs` output equals the recorded server responses on the probe set
- [ ] On development runs, ≥ 99 % of expected citations resolve to an evidence unit (the rest listed)
- [ ] Setup for a full run finishes in ≤ 3 min within 4 GB memory
- [ ] Untrusted-flag recall/false positives measured against the injection-landscape templates on development runs

Spec: [SPEC.md](../SPEC.md)
