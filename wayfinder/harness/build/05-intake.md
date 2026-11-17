# 05: Intake

**What to build:** Each task gets one Jev call that picks its family from a catalogue of written family descriptions (never dev wording), plus task kind and the allowlist of operations; low confidence or 'other' routes to the loop. The workspace `/AGENTS.md` and the operator files it names are read as the only trusted text, and their hard rules, outcomes and source tiers are prepared for the loop and compose prompts.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] Family and task-kind accuracy measured on development tasks (target ≥ 95 %), confusion matrix saved
- [ ] Allowlist matches the grader's task-kind mapping on development tasks
- [ ] Intake costs ≤ $0.001 per run in Jev spend

Spec: [SPEC.md](../SPEC.md)
