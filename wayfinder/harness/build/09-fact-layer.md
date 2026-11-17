# 09: Fact layer and the instruction judgment

**What to build:** Per run and per audit-log/board version, code computes the shared facts (SPEC §5): decoders, the 'instruction to agents?' judgment (Jev yes/no, Gemma for the uncertain band, cached by hash), attack traces, compromised set, patient zero, exfiltration transfers, attacker domains, permission graph with delegations, standing-order settings (from the transcript once images exist), roster and contacts. Every scenario assumption is a checked precondition.

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] Judgment accuracy vs. the development runs' expected traces, and the Jev/Gemma split, measured and recorded
- [ ] Derived facts match what the development runs' expected answers imply for the core families
- [ ] No dev values in code (review checklist)

Spec: [SPEC.md](../SPEC.md)
