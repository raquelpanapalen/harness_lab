# 12: Solver wave 3: inbox routing

**What to build:** Inbox requests are routed by code: sender verification, roster name resolution (refuse only on > 1 match), unsupported requests, and the OK actions (block domains, message a verified contact with the required facts, quarantine one agent) proposed through compose and the guards.

**Blocked by:** 07, 10

**Status:** ready-for-agent

- [ ] Outcome accuracy on development inbox tasks, including all three refusal kinds
- [ ] Zero constraint failures on these tasks

Spec: [SPEC.md](../SPEC.md)
