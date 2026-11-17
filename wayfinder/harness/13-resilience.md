# Provider errors, retries and pacing

Label: `wayfinder:grilling`
Status: open
Assignee: 
Blocked by: 06-pipeline-architecture.md
Map: [New harness for Harness Arena](map.md)

## Question

Two thirds of the baseline's lost points were provider errors (429 under 8 parallel workers; 400 on prompts above ~8k
tokens) that the kit never retried. How does the harness pace calls inside 30 minutes (workers per model, a shared rate
limiter), retry 429/400/5xx (backoff, shrinking context on 400, logging `e.body` and the provider), checkpoint so a
task resumes rather than restarts, and guarantee a submission before the deadline or the $3 limit (403)?
