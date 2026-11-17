# Model capabilities and costs

Label: `wayfinder:research`
Status: closed
Assignee: Raquel (research agent)
Blocked by: 
Map: [New harness for Harness Arena](map.md)

## Question

For google/gemma-4-31b-it on OpenRouter (with the final's provider order), typesafe/jev-1.14 and qwen/qwen3-embedding-8b: context length, prices, tool-calling and JSON reliability, reasoning settings, latency and rate limits (FINAL_RUN.md), known quirks. What is Jev for (decisions API) and where would it beat Gemma? When do embeddings beat BM25 for workspace retrieval? No paid calls: docs, model cards, repo files, existing traces.

## Resolution

Findings: branch `research/model-capabilities`, file `research/model-capabilities.md` (pushed, not merged). No paid
calls. Behaviour statistics from 8 baseline trace runs (350 tasks, 6,464 Gemma calls; this includes held-out runs'
traces, read for call mechanics only, before the held-out-trace decision, never for task content).

- Gemma (final pins DeepInfra fp8): $0.20/M in, $0.40/M out, 16k max output, no prompt caching. Cost is almost all
  input, because history is re-sent each step: ≈ 7–8k input tokens/call, ≈ 19 calls/task, $1.26–1.57 per run. Levers:
  less context per step, fewer steps.
- Tool-call syntax reliable (0 malformed args, ~1 call/turn); judgment isn't. 24 % of submits sent `answer` as a
  JSON-encoded string (harness must coerce). 48 % of tasks hit the 25-step cap; L3 used only ~25 % more steps than L1.
- Thinking off by default; on per call via `reasoning: {effort}` (billed as output, counts toward max_tokens). Whether
  effort levels differ on Gemma is unconfirmed.
- Jev: $0.042/M input, output free (~$0.00006/decision). Typed `noul` / `choice` / `score` questions, batched,
  calibrated probabilities. Fits injection/authority screening, outcome second opinion, "enough evidence to stop?",
  relevance filtering. Bad at counting, arithmetic, dates; degrades with irrelevant state; > ~32 KB input switches to a
  slower long-context mode. Keep concurrency ≈ 10.
- Embeddings: $0.01/M (fallback $0.04/M), ≈ 8 s per 45k-token request under load; whole workspace ≈ $0.02/run. For
  ids in logs/csv/json, regex/SQL/query_logs win; embeddings help on prose (policies, inbox, documents), combined with
  BM25.
- Budget: $3/48 = $0.0625/task ceiling. The agent suggests targeting ≈ $0.50/run (≈ $0.005/0.010/0.016 per task for
  L1/L2/L3), based on the real score's cost factor; note our goal treats cost as a hard limit only (revisit in Model
  roles and budget per level).
- Open: does OpenRouter honour `dimensions` for the embedding model; does the final gateway forward `GET /api/v1/key`
  (live spend); image token cost on DeepInfra.
