# 01: Prefactor: selectable harness, shared parsers

**What to build:** The run entry point can run either the kit baseline or the new harness (a flag, baseline the default until the new one wins), and the file parsers (pdf, docx, xlsx, ods, odt) live in a shared module both use. The baseline behaves exactly as before.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Choosing the baseline produces the same behaviour and trace format as today (one-task smoke run on the replay)
- [ ] Choosing the new harness reaches a stub that starts the run and finishes it cleanly
- [ ] Parsers are shared, not duplicated; the baseline uses them
- [ ] Commit + JOURNAL entry

Spec: [SPEC.md](../SPEC.md)
