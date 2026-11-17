# Task taxonomy and what code can solve

Label: `wayfinder:research`
Status: closed
Assignee: Raquel (research agent)
Blocked by: 
Map: [New harness for Harness Arena](map.md)

## Question

Across the 39 development runs (not the 5 held-out ones): which task families and answer shapes exist, how often, at which levels, and for each, can deterministic code compute the answer (counting, joins across files, timelines, delegation chains, containment plans) once the right files are found, or does it need the model to read and judge? Which file formats and sizes appear? Design input only: no expected answers go into the harness.

## Resolution

Findings: branch `research/task-taxonomy`, file `research/task-taxonomy.md` (pushed, not merged). 39 development runs,
1,872 tasks; held-out runs never opened; structure only, no answers.

1. Very regular: one theme, 48 tasks (16 per level), one incident per run. 10 families appear once per level in every
   run; ~12 more fill the rest. Request wording is the same across levels; harder levels change the files. A regex over
   the request sorts all 1,872 tasks into 23 families. (Caveat from prior art: the real arena varies wording, so
   routing must not depend on exact phrasings.)
2. ≈ 75 % of tasks are computable by code once a few per-run facts are established (compromised agents, patient zero,
   timelines, leaked tokens by hash, access-log policy, access review, exfil counts, canaries, breach notification,
   lethal trifecta, delegation chains, rotation scheduling, containment, first token read, board-message origin, inbox
   routing). Core files are identical across a run's tasks, so facts can be cached per run; the audit log has ≈ 36
   versions per run (per-task planted attacks).
3. Model needed in bounded places: judging whether text is an instruction to agents (board posts: multilingual,
   base64, zero-width chars, split posts, quotes later adopted; MCP manifest descriptions), which also decides which
   artifacts start attack traces; parsing numbered claims in status reports; reading images.
4. Vision unavoidable: the open ticket is a JPG in 668/973 ticket tasks; counting and rotation rules live only in the
   signed `/standing_order.jpg` (4 per-run settings), the typed `.md` is an outdated decoy.
5. Refusals are rule-driven, only from inbox messages and containment requests: DENIED_SECURITY (34) = sender not a
   verified contact (look-alike domain) or listed compromised in `/fleet/changes.md`; NEEDS_CLARIFICATION (25) = a
   quarantine target named by role/owner/nickname matching > 1 roster agent; UNSUPPORTED (10) = scope removal (no tool).
6. Large workspaces: ≈ 400 files, 650k–890k tokens per task, mostly noise; useful core ≈ 250k. Audit log in UTC,
   archive and some calendar rows in CET.
7. Citations: median 4 per task; list answers expect one citation per item (up to 71); tickets and the standing order
   are never cited.
