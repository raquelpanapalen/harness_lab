# 13: Solver wave 4

**What to build:** Solvers for exfiltration counts/domains/blast radius, canary records, breach notification, containment plans, rotation scheduling, first token read, token status and board-message origin, applying the standing-order settings.

**Blocked by:** 07, 08, 10

**Status:** done

- [x] Per-family Score: solver vs. loop on development runs
- [x] Containment and rotation tasks pass the guards with zero constraint failures

Spec: [SPEC.md](../SPEC.md)

## Done

- `harness/wave4.py` holds plain functions over the facts layer; `harness/solvers/wave4.py` registers them. The
  families:
  - exfiltration: records and domains, top team, top agent and owner e-mail, first success, first domain per agent;
    "distinct" forces dedupe;
  - canaries;
  - breach notification: the runbook template lines, filled and sent to the DPO;
  - containment: everything the runbook calls for, or the named targets, NEEDS_CLARIFICATION on an ambiguous name,
    roster stand-ins, rotations at `guards.schedule_rotation`;
  - rotation, first token, board origin;
  - token status: confident only when the audit log shows the revocation;
  - two families no wave covered: attacker domains (inline lists, scanned pages, "never contacted"; planted
    notes/JSON on scans ignored by reading only short plain lines; gap checks go to the loop) and partner drafts
    (incident id, record count, domains, patient-zero time; no links or e-mails).
- Per-family dev measurement (39 runs, `harvester/eval_wave4.py`; the loop scored 0.00–0.39 on these families):

| family | dev tasks | correct | confident | confidently wrong |
|---|---|---|---|---|
| inbox | 117 | 117 | 117 | 0 |
| containment | 76 | 76 | 76 | 0 |
| exfiltration | 126 | 126 | 126 | 0 |
| breach_notification | 57 | 57 | 57 | 0 |
| canaries | 34 | 34 | 34 | 0 |
| partner_draft | 60 | 60 | 60 | 0 |
| first_token | 30 | 30 | 30 | 0 |
| rotation | 41 | 41 | 41 | 0 |
| board_origin | 15 | 15 | 15 | 0 |
| attacker_domains | 117 | 117 | 95 | 0 |
| token_status | 23 | 17 | 5 | 0 |

- Replay with every wave on (waves 1, 2, 4, 5 and inbox, guards):

| run (all waves on, same seeds as rp008/rp009) | A | cost | C | Score |
|---|---|---|---|---|
| rp002_run_NLzokmjwLUfD | 0.934 | $0.017 | 1.000 | 0.934 |
| rp003_run_ObCe2yzg_xMy | 0.924 | $0.028 | 1.000 | 0.924 |

  Mean Score 0.929 at $0.023 per run (goal < $0.05). There were 0 constraint failures; rotations and containments
  all passed the guards. The loop ran on 2 tasks in total. rp002 lost t04 (MCP) to a solver putting an evidence
  unit in `sources`; fixed in 544174e (submission cleans sources, empty answer as the last rung) and 61eef53.
