# Model roles and budget per level

Label: `wayfinder:grilling`
Status: closed
Assignee: Raquel (session)
Blocked by: 04-model-capabilities.md, 06-pipeline-architecture.md
Map: [New harness for Harness Arena](map.md)

## Question

Which model does what (Gemma loop, Jev decisions, embeddings), and how are the $3 and 30 minutes spread across 48 tasks and three levels: step budgets, retries, when to stop?

## Resolution

Decided with the human (grilling, two rounds; the second after reading the leaderboard, arena.ai-mission.eu/leaderboard):

The leaderboard ranks by Score = A · (0.5 + 0.5 · C), C on a log scale from 1 at $0.049/run to 0 at $1.135/run
(event range, checked against several rows). AIT won with A 0.751 at $0.049 (Score 0.750); Donaufriends A 0.765 at
$0.354 (0.524). At A 0.75: $0.05 → 0.75, $0.10 → 0.66, $0.30 → 0.53, $0.50 → 0.47.

1. Objective = leaderboard Score with C on the event's fixed range; every evaluation reports A, cost, C, Score. Design
   choices are judged on Score. Bars: 0.750 (AIT), 0.524 (Donaufriends). This replaces "maximise accuracy, cost a hard
   limit only".
2. Model roles v1: Gemma for loop steps, compose, image transcription, uncertain instruction judgments, status-claim
   parsing; Jev for intake and "instruction to agents?" yes/no; no embeddings. Jev "enough evidence to stop?" is a later
   experiment.
3. Budget target ≤ $0.10 per run (≈ $0.002/task on average; C ≥ 0.77). Code solvers and Jev carry most tasks; Gemma
   only where a solver can't answer.
4. Loop limits 6 / 8 / 10 steps by level, prompts ≤ ~6k tokens (≤ ~$0.01 for an L3 loop task), each tuned by its
   effect on Score. Run watcher: no new loop steps at $0.50, FALLBACK_SUBMIT at $1.00 (runaway guard, not a budget).
5. Per family, if dev data shows the loop scores near 0, that family submits the best code-only answer instead of
   looping.
6. Reasoning off for loop and compose; first experiment after v1: compose with reasoning low on L2/L3. Temperature 0.
7. Reopened Image inputs (see its addendum): read only the images a task needs, at the lowest accurate resolution.
