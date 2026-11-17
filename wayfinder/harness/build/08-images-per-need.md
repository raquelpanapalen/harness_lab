# 08: Images per need

**What to build:** Images are transcribed only when a task needs them (tickets until the first OPEN one, the standing order once per run, scans for scan tasks), cached by blob hash, with one generic verbatim-plus-marks prompt; transcripts become untrusted, path-cited evidence units. The image budget per image kind is chosen from a measurement on development images (scanned domains, standing-order settings, ticket status).

**Blocked by:** 04, 06

**Status:** ready-for-agent

- [ ] Measurement table: accuracy and cost per image kind and budget
- [ ] Image spend per run reported; chosen budgets recorded in the JOURNAL
- [ ] Ticket-queue tasks find their open ticket on development runs

Spec: [SPEC.md](../SPEC.md)
