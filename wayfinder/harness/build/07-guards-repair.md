# 07: Guards and repair

**What to build:** State-changing actions are proposed only by compose and pass the deterministic guards before anything reaches the server (SPEC §7): allowlist (emptied on any refusal), argument checks (roster and evidence for quarantines, stand-ins, scheduler or calendar for rotations, verified named recipients, evidence-backed domains, named write path), the frame guard on agent ids, outgoing text rewritten (defang, strip secrets and operator-rules text), refusals only with a code-verified reason, and one repair round.

**Blocked by:** 05, 06

**Status:** ready-for-agent

- [ ] Zero constraint failures on development runs
- [ ] Unit tests per guard, including the injection-landscape attack shapes (framing via audit `detail`, live URLs, token requests)
- [ ] Blocked actions are never sent (verified in the replay's action log)

Spec: [SPEC.md](../SPEC.md)
