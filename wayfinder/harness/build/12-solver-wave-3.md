# 12: Solver wave 3: inbox routing

**What to build:** Inbox requests are routed by code: sender verification, roster name resolution (refuse only on > 1 match), unsupported requests, and the OK actions (block domains, message a verified contact with the required facts, quarantine one agent) proposed through compose and the guards.

**Blocked by:** 07, 10

**Status:** done

- [x] Outcome accuracy on development inbox tasks, including all three refusal kinds
- [x] Zero constraint failures on these tasks

Spec: [SPEC.md](../SPEC.md)

## Done

- `harness/inbox.py` routes the inbox message in code, in this order:
  1. The sender from the header: not a verified contact, or flagged in the change log, gives DENIED_SECURITY with
     the sender as the answer.
  2. Scope or permission removal ("take `read:billing` away …") gives UNSUPPORTED.
  3. Quarantine resolves its reference (id, name, nickname, role, owner, "X needs to go into quarantine") against
     the roster by whole values. More than one match gives NEEDS_CLARIFICATION with every candidate; one gets the
     stand-in.
  4. Block takes the domains the message names, intersected with the runbook's attacker domains when it says
     "every attacker domain from that list".
  5. Send goes to the address the message gives, or to the verified contact whose role or name it mentions. The
     body carries the facts it asks for, computed in code (attacker domains, record count, incident id), plus the
     request.

  A confident decision sets the task's kind and allowlist; a refusal empties it. The guards still check every
  action.
- Measured on 39 dev runs (`harvester/eval_wave4.py --family inbox`): 117/117 correct, all confident, 0 confidently
  wrong: DENIED 34, NEEDS_CLARIFICATION 14, UNSUPPORTED 10, send 28, block 16, quarantine 15.
- Tests: `tests/test_lab_wave4.py`. Replay: see ticket 13 (combined runs, 0 constraint failures).
