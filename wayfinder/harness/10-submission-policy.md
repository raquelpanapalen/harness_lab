# Submission and fallback policy

Label: `wayfinder:grilling`
Status: closed
Assignee: Raquel (session)
Blocked by: 01-baseline-failure-analysis.md, 02-task-taxonomy.md
Map: [New harness for Harness Arena](map.md)

## Question

How is each answer shaped and checked before submit (answer formats per question type, outcome choice, how many citations), and what does the harness submit when it runs out of steps, budget or errors?

## Resolution

Decided with the human (grilling, all recommendations accepted):

1. Compose fills a schema built from the instruction's stated format; code normalises: JSON strings parsed, numbers as
   numbers, top-level timestamps rewritten to `YYYY-MM-DDTHH:MM:SSZ` in UTC (CET archive converted), duplicates
   removed, lists sorted when the instruction says sorted, object keys exactly as named.
2. Outcome is OK unless a non-OK outcome comes with a code-verified reason: DENIED_SECURITY = sender not a verified
   contact or flagged in the change log; NEEDS_CLARIFICATION = target matches > 1 roster agent; UNSUPPORTED = no tool
   can do it. An unverified refusal goes back to compose as OK in the repair round.
3. Citations: the evidence units the answer rests on (one per item for list answers), plus the rule file when the
   answer applies its rule. Never cite the ticket or inbox message carrying the task, unless the answer is about that
   message (refusals). Cap = answer items + 2, or 4 for single-value/object answers.
4. Submit as soon as check passes. Fallback ladder: solver answer (even unconfident) → compose from the current
   notebook → right-shaped empty answer (`[]`, `{}`, `null`) with outcome OK and no citations. Never a refusal as
   fallback. Trigger numbers (deadline, budget) are set in the pacing and budget tickets.
