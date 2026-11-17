# Family solvers: which, and how they fail safe

Label: `wayfinder:grilling`
Status: open
Assignee: 
Blocked by: 01-baseline-failure-analysis.md, 06-pipeline-architecture.md
Map: [New harness for Harness Arena](map.md)

## Question

The taxonomy says ≈ 75 % of tasks are computable by code from a few per-run facts. Which families get a code solver,
in what order (by points recoverable), how does a solver recognise its task without depending on exact dev wording, and
what happens when its inputs don't parse (fall back to the generic loop)? Which per-run facts are computed once and
cached, and how are per-task file variants (the audit log's planted attacks) kept from leaking between tasks?
