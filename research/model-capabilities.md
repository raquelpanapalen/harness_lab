# Model capabilities and costs (Gemma 4 31B, Jev 1.14, Qwen3-Embedding-8B)

Research ticket for the new harness. Researched 2026-11-17. No paid model or arena calls were made: facts come
from provider docs / OpenRouter's public model metadata, and behaviour statistics come from the existing baseline
traces (`runs/*/*.jsonl`, 8 runs, 350 tasks, 6,464 Gemma tool calls; `harvester/replay_results/`). Trace numbers
are model-behaviour only (no task content used). Scripts that produced them: see "Method" at the end.

Legend: **[doc]** = primary source, **[trace]** = measured in our traces, **[derived]** = my calculation from the two.

---

## TL;DR for harness design

1. **Gemma's cost is almost entirely input tokens, re-sent every step.** Baseline: ~$0.0016 per Gemma call,
   ~19 calls per task, **$0.026–0.033 per task, $1.26–1.57 per 48-task run** [trace]. The pinned endpoint
   (DeepInfra fp8, $0.20 / $0.40 per 1M in/out) has **no prompt caching** [doc], so a tool loop's cost grows with
   (steps × context). Fewer steps and a smaller carried context are the main levers; output tokens barely matter.
2. **$3 / 48 = $0.0625 per task is a ceiling, not a target.** Cost is half the score on a log scale vs. the field:
   with a 10× span, halving spend adds +0.15 to the multiplier `0.5 + 0.5·C`. Aim for ≤ $0.01/task
   (≈ $0.50/run) unless accuracy gains are large (rule of thumb below).
3. **Gemma tool calling is very reliable syntactically** [trace]: 0 malformed JSON argument strings in 6,464 calls,
   always exactly one tool call per turn (2 exceptions), never text alongside a call, `finish_reason` always
   `tool_calls`. **Semantic** formatting is the weak spot: 24 % of submits passed `answer` as a JSON-encoded
   *string* (`"[\"C\", \"A\"]"`) instead of an array; 48 % of tasks ran into the 25-step cap and were force-submitted.
4. **Thinking is off by default** for Gemma via OpenRouter (0 of 6,464 responses carried `reasoning`) [trace].
   Turn it on per call with `reasoning: {effort: ...}` only where it pays (planning, final answer check).
5. **Jev is ~5× cheaper per input token than Gemma ($0.042 vs $0.20 per 1M), output free, ~0.2–0.6 s**, and returns
   calibrated probabilities over options *you* define. Use it for yes/no and pick-one decisions over short state:
   injection/authority screening, "is this message from a verified contact asking for X", outcome routing
   (OK / DENIED / CLARIFY / UNSUPPORTED) as a second opinion, "is this evidence sufficient → stop or continue",
   ticket OPEN/CLOSED. Not for counting, arithmetic, dates or extraction [doc].
6. **Embeddings are nearly free ($0.01/1M) but slow under load (≈8 s per 45k-token request).** For logs/CSV/JSON
   with ids, use deterministic search (the `query_logs`/`search` tools, local SQL/BM25). Embeddings earn their
   place on prose (runbook, policies, reports, inbox, converted pdf/docx) where the final's unseen phrasings won't
   share words with the target passage; hybrid (BM25 + embedding, reciprocal-rank fusion) is the safe default.

---

## 1. Gemma 4 31B (`google/gemma-4-31b-it`) — the main model

### Specs [doc]
| Property | Value | Source |
|---|---|---|
| Size / type | 30.7B dense, multimodal (text + image in, text out); ~550M vision encoder; 60 layers, 1024-token sliding window | Gemma 4 model card |
| Context | 256K (OpenRouter endpoints: 262,144) | model card; OpenRouter endpoints API |
| Max output on the pinned endpoint | **16,384** tokens (DeepInfra fp8); Crusoe 262,141; Novita 131,072 | OpenRouter endpoints API |
| Knowledge cutoff | January 2025 (pre-training data) | model card |
| Benchmarks (IT) | MMLU-Pro 85.2, GPQA-D 84.3, LiveCodeBench v6 80.0, **τ2 76.9** (tool use), MRCR v2 8-needle 128k 66.4, BBEH 74.4 | model card |
| Recommended sampling | temperature 1.0, top_p 0.95, top_k 64 | model card |
| Image budgets | 70 / 140 / 280 / 560 / 1120 tokens per image; higher for OCR/small text; put images before text | model card |

MRCR 66.4 % at 128k: long-context recall is decent but not reliable; don't stuff 100k+ tokens and expect needles found.

### Price on the endpoints the final uses [doc]
The gateway forces `deepinfra/fp8 → crusoe → novita` (FINAL_RUN.md; `PROVIDER_ORDER` in `arena/llm.py`).

| Endpoint | Input $/1M | Output $/1M | Cache read | `tools` / `tool_choice` | `response_format` / `structured_outputs` |
|---|---|---|---|---|---|
| deepinfra/fp8 | **0.20** | **0.40** | none | yes; `auto`, `required`, named function; **`none` unsupported** | yes / yes |
| crusoe/bf16 | 0.14 | 0.40 | 0.14 (= no discount) | yes; `auto`, `required`, function; `none` unsupported | yes / yes |
| novita/bf16 | 0.14 | 0.40 | none | yes; all four | yes / yes |

Source: `https://openrouter.ai/api/v1/models/google/gemma-4-31b-it/endpoints` (fetched 2026-11-17). Note the
listing also shows `deepinfra/fp8/flex` ($0.16/$0.32) and `deepinfra/fp8/priority` ($0.30/$0.60) tiers; the
gateway pins `deepinfra/fp8`, so budget with $0.20/$0.40. Novita showed degraded uptime (23 % last 30 min, 85 %
last day) at fetch time.

Practical consequences:
- **No prompt caching** on the primary endpoint → every step pays for the whole prompt again (system prompt, tool
  schemas, history). Summarise/trim history aggressively; a stable prefix buys nothing here.
- `tool_choice: "none"` may fail on DeepInfra/Crusoe: to get a text-only answer, omit `tools` instead.
- Structured output (`response_format: json_schema`) is supported on all three pinned endpoints, so a
  "give me the final answer as JSON" step can use a schema instead of a `submit` tool.

### Reasoning / thinking control
- Gemma 4 native: thinking is on when the system prompt starts with `<|think|>`; thought goes in a
  `<|channel>thought … <channel|>` block. With thinking off, the 31B still emits an *empty* thought block. In
  multi-turn, drop earlier thoughts — **except across tool-call turns, where thinking should be preserved** [doc,
  model card].
- OpenRouter: `reasoning: {effort: "none"|"minimal"|"low"|"medium"|"high"|"xhigh"|"max"}` or
  `reasoning: {max_tokens: N}`; `enabled: true` = medium; `exclude: true` hides it but still bills it. Effort maps to
  a share of the output limit (low ≈ 20 %, medium ≈ 50 %, high ≈ 80 %). **Reasoning tokens are billed as output and
  count toward `max_tokens`**; if they exhaust it, `content` is null and the tokens are still billed. To keep
  reasoning across tool calls in Chat Completions, pass back `reasoning_details` (or `reasoning`) [doc, OpenRouter
  reasoning-tokens guide]. The guide does not say how effort levels map for an on/off model like Gemma —
  **unverified whether `low` vs `high` differ for Gemma**; measure on a small subset.
- **Default is off** [trace]: the baseline never sets `reasoning`, and 0 of 6,464 responses had a `reasoning` field.
- Cost of thinking [derived]: output is $0.40/1M, so even 1,000 thinking tokens = $0.0004 — cheap per call compared
  with the ~$0.0016 input cost of a typical baseline step. Thinking on a *few* decisive calls is affordable; thinking
  on every loop step roughly adds 25 %+ per step and adds latency. The real cost risk is not the thought tokens but
  that thinking text, if kept in history, inflates every later input.

### Observed behaviour in the baseline traces [trace]
8 runs (7 × 48 tasks + 1 × 6), kit baseline, temperature 0.2, no reasoning, `max_steps` 25, parallel 4.

| Metric | Value |
|---|---|
| Run cost (local tally, max cumulative) | $1.26, $1.30, $1.34, $1.44, $1.48, $1.55, $1.57 per 48 tasks |
| Cost per task | $0.026–0.033 |
| Gemma calls per run | 839–959 (17.5–20.0 per task; median 21–25) |
| Cost per call | $0.0015–0.0017 → **≈ 7–8k input tokens per call** at $0.20/1M [derived] |
| Steps by level (mean) | L1 14.5–18, L2 18–20.5, L3 19.4–22.4 — little difference: the loop doesn't adapt to level |
| Tasks that hit the 25-step cap | 168 / 350 (48 %) → forced `submit` via `tool_choice` |
| Latency per model step | median 2.6–3.0 s, p95 9.6–13.9 s, max 136 s (includes SDK retries) |
| Task wall time | median 68–103 s, max ~500 s |
| Malformed tool-call JSON | **0 / 6,464** |
| Tool calls per assistant message | 1 in 6,436 messages; 2 and 5 once each (parallel calls are rare) |
| Text + tool call in one message | 0 |
| Empty responses (no text, no tool) | 0 |
| `finish_reason` | always `tool_calls` |
| Submit `answer` as JSON-encoded string | **79 / 330 (24 %)** — coerce `"[...]"` strings to arrays |
| Submit without sources | 29 / 330 |
| "No choices" responses (provider 504 timeouts after ~15 s) | 12 tasks lost in 3 runs before commit b7049a6 added a retry; 0 after |
| Tool output size | median ~1.0–1.3k chars, p95 2k (kit truncates) |
| `view_image` calls | 56–72 per run |

Interpretation: Gemma follows the function-calling protocol cleanly, so a harness can rely on it for routing and
argument filling. It is weak at *knowing when it's done*: half the tasks ran to the step cap. Hard step budgets
per level, an explicit plan step, and a cheap "enough evidence?" check (Jev) are where cost goes down.

### Latency and rate limits [doc: FINAL_RUN.md]
Rehearsal with 12 finals in parallel: Gemma median 3.0 s, p95 9.2 s, slowest 67 s, no errors. Task API median
0.08 s, ~20 req/s per team (bursts 100, then 429 + `Retry-After`). Kit final with `--parallel 4` took 8–10 min
and cost **$0.66–0.81** (the rehearsal scenario; our replay runs cost about 2× that). OpenRouter publishes no
fixed RPS for paid models; 402 with `Retry-After` = in-flight budget full (retry), key-limit exhaustion is final
(FINAL_RUN.md says 403 `Key limit exceeded`) [doc, OpenRouter limits page]. `GET /api/v1/key` returns `usage`
and `limit_remaining` (free), which a harness could use to throttle spend — **unverified whether the organisers'
gateway forwards `/key`**.

Throughput sizing [derived]: 48 tasks × 19 steps × 3 s ≈ 46 min serial → need ≥ 4 parallel workers to fit 30 min
with margin; 8 is safer once per-step latency tails (p95 10 s) are counted.

---

## 2. Jev 1.14 (`typesafe/jev-1.14`) — typed decisions

### What it is [doc]
A "System One" **decision model, not an LLM**: you send `state` + named typed questions, it returns probabilities.
No text generation, no reasoning, no explanations. Probabilities are "trained for calibration", measured across
groups, not a per-answer guarantee.

### Price, context, limits [doc]
| Property | Value | Source |
|---|---|---|
| Price | **$0.042 per 1M input tokens; output free** | OpenRouter endpoints API (`prompt: 0.000000042`); docs.typesafe.ai/models |
| Example cost | 476 input tokens → $0.000019992 | OpenRouter Decisions API reference |
| Context | 262,144 on OpenRouter. TypeSafe native: per-request 64k / per-question 32k by default; 262k only with the long-context header | OpenRouter; docs.typesafe.ai/models |
| Long-context mode | OpenRouter: state text over ~32,000 **bytes**, or any image, runs in long-context mode — "slower and less accurate" | OpenRouter Jev guide |
| Inputs | text; PNG/JPEG images (early feature). (One OpenRouter blog post says no images — conflicts with TypeSafe's models page.) | docs.typesafe.ai/models; OpenRouter guide |
| Questions per request | no limit; evaluated in parallel and independently ("cannot see each other's answers"); batching 13 questions was "12.2× cheaper and 11.8× faster" than 13 calls | docs.typesafe.ai/primitives |
| Version | `typesafe/jev-1.14` (snapshot `jev-1.14-20261014`); same price as 1.13, adds `box` | OpenRouter; TypeSafe models |

### Request / response format [doc: OpenRouter Decisions API reference]
`POST {OPENROUTER_BASE_URL minus /v1}/alpha/decisions` (gateway: `/openrouter/api/alpha/decisions`), Bearer key.

```json
{"model": "typesafe/jev-1.14",
 "state": {"message": "...", "verified_contacts": ["..."]},        // string | object | array
 "questions": {
   "inj":  {"type": "noul",   "instructions": "...", "criteria": {"true": "...", "false": "..."}},
   "kind": {"type": "choice", "instructions": "...", "criteria": {"ok": "...", "denied": "...", "clarify": "..."}},
   "urg":  {"type": "score",  "instructions": "...", "criteria": ["low", "medium", "high"]}}}
```
Response: `{id, model, provider, answers: {inj: {type:"noul", noul: 0.96}, kind: {type:"choice", choice:"ok",
confidence:0.75, probabilities:{...}}, urg: {type:"score", score:1.99, confidence:0.99, legend:{...},
probabilities:{...}}}, usage: {input_tokens, output_tokens, cost, ...}}`. An answer can also be
`{type: "refusal"}` with no values (others in the same request still succeed). Errors: 400 bad params, 402 credits /
in-flight budget, 413 too large, 429, 5xx/524 timeouts. `arena/llm.py` `decide()` already wraps this (note:
it builds `score` criteria as two endpoints only; the API accepts any ordered list of levels).

### Confidence semantics [doc: docs.typesafe.ai/confidence]
- `noul`: use the probability; suggested starting bands **> 0.7 yes, < 0.3 no, 0.3–0.7 its own outcome** (escalate).
- `choice`: `confidence = (p_max − 1/n)/(1 − 1/n)`; threshold the probability of the option you will act on; for
  close calls check top-to-second ratio.
- `score`: confidence is based on mean absolute deviation from the top level; check confidence before trusting the
  position.
- Tune thresholds on labelled data by the cost of each error type, not on round numbers.

### Where Jev beats Gemma [doc + derived]
OpenRouter's comparison (their data, small sets): ticket triage 59/60 intent, 60/60 escalation at p50 0.194 s and
$0.0248 per 1,000; **prompt-injection screening 40/40** at $0.0161 per 1,000. Jev is "unreliable at arithmetic,
counting, and date comparison"; accuracy **drops with irrelevant state**; reads criteria literally; rewording one
option shifts the others [doc: OpenRouter "Jev vs LLM" blog].

Per-decision cost [derived]: a 1,500-token state with three questions ≈ $0.00007 on Jev. The same decision as a
Gemma call costs ≥ $0.0003 (1,500 tokens + system prompt) and as a step inside the baseline loop ≈ $0.0016 (full
history re-sent). Good Jev jobs in this arena:
- **Injection / authority screening** of each untrusted chunk (inbox message, board post, doc paragraph, tool
  output) — annotate, never act on it alone (the kit's `--jev-inbox-check` pattern).
- **Outcome routing second opinion**: `choice` over OK / DENIED_SECURITY / NEEDS_CLARIFICATION / UNSUPPORTED with the
  AGENTS.md rules + the request in `state`; disagreement with Gemma → spend a thinking call.
- **Stop/continue**: `noul` "does the collected evidence answer the question?" to cut the 48 % step-cap rate.
- **Relevance filter** before Gemma reads: `noul`/`score` per candidate chunk from retrieval (cheaper than letting
  Gemma read them). Keep each state small and focused (one chunk + the question).
- **Image yes/no** (ticket stamp OPEN/CLOSED, "does this screenshot show X") — an early feature, and any image forces
  long-context mode; verify on dev before relying on it; Gemma vision remains the reader for values.
Not for: extracting ids/values, counting log events, comparing timestamps, producing answers — do those in code or
with Gemma.

### Latency [doc: FINAL_RUN.md]
10 parallel calls on one key: median 0.4 s, p95 0.6 s (693 calls in 30 s). 25–100 parallel: throughput flattens at
~7 calls/s and latency rises to 3–11 s (p95 up to 24 s). **Cap Jev concurrency at ~10** and batch questions per
state rather than issuing many single-question calls.

---

## 3. Qwen3-Embedding-8B (`qwen/qwen3-embedding-8b`)

### Specs and price [doc]
| Property | Value | Source |
|---|---|---|
| Params / context | 8B; 32K tokens per input (DeepInfra 32,768; Nebius 40,960) | HF model card; OpenRouter endpoints API |
| Dimensions | up to 4096; MRL, user-defined 32–4096 (model card). OpenRouter lists **no supported parameters**, so `dimensions` is not known to be honoured — truncate + renormalise locally if you want smaller vectors | HF model card; OpenRouter |
| Price | **DeepInfra $0.01 / 1M**; SiliconFlow (gateway fallback) **$0.04 / 1M**; Nebius $0.01 | OpenRouter endpoints API |
| Instructions | instruction-aware: prefix **queries only** with `Instruct: {task}\nQuery:{query}`; documents plain; worth +1–5 % | HF model card |
| Pooling / norm | last-token pooling, L2-normalised (cosine = dot product) | HF model card |
| Quality | MTEB Multilingual 70.58 (No. 1 on 2025-06-05), MTEB Eng v2 75.22 | HF model card |
| Latency under load | 17 parallel clients, ~45k tokens/request: median 7.8 s, p95 11.8 s | FINAL_RUN.md |

### When embeddings beat BM25 here [doc + derived]
- BEIR (Thakur et al., 2021): "BM25 is a robust baseline"; dense retrievers have a generalisation gap out of domain;
  re-ranking is best but expensive. In practice that means **hybrid**, not dense-only.
- **Logs (JSONL audit events), CSV/JSON registries, token inventories**: queries are ids, agent names, actions,
  domains, time windows. Exact match / structured filters win outright (`query_logs` with `agent/action/target/since`,
  `search` with regex, or local SQLite). Embeddings add nothing and would cost minutes of latency to index
  thousands of lines.
- **Prose (AGENTS.md-named runbook/policy sections, reports, inbox, board posts, pdf/docx/odt text)**: embeddings help
  when the instruction's wording differs from the document's (docs.md: final instructions are "worded in phrasings
  no dev run shows"), e.g. "notify the regulator" vs "supervisory authority". Use BM25 + embeddings fused with RRF.
- **Cost** [derived]: slide 21 says a task workspace is ~370 files, 1–2M tokens; most files are shared across a run's
  tasks (content-addressed). Embedding all text once per run: 2M × $0.01/1M = **$0.02** (or $0.08 if SiliconFlow
  serves). Embedding only prose files is likely ≪ 1M tokens → < $0.01. Queries are negligible.
- **Latency is the real cost**: at ~45k tokens per request and ~8 s each, 2M tokens = ~45 requests ≈ 1 min with 8
  in parallel. Embed lazily (prose only, first time a file is needed) or at startup in the background, and cache
  vectors by sha256.

---

## 4. Cost model and budget

### Score sensitivity [doc: docs.md scoring; derived]
`final = A · (0.5 + 0.5·C)`, `C = 1 − (ln cost − ln floor)/(ln cap − ln floor)`, final span ≥ 10×.
- With a 10× span, each **halving of cost adds 0.30 to C → +0.15 to the multiplier**. From C = 0.5 (multiplier 0.75),
  halving cost is worth +20 % relative score; doubling cost must buy > 20 % relative accuracy to break even.
- Floor/cap come from the field (runs with ≥ half the top accuracy); kit-like spend ($0.7–1.5) is likely mid-to-high.

### Per-call cost formulas [derived from §1–3 prices]
| Call | Formula | Typical |
|---|---|---|
| Gemma (DeepInfra fp8) | $0.20 × in_M + $0.40 × out_M | 8k in + 150 out ≈ **$0.0017** (baseline step); 3k in + 300 out ≈ $0.0007 |
| Gemma + thinking | add $0.40 per 1M thinking tokens | +1k thinking ≈ +$0.0004 |
| Gemma vision | image tokens (70–1120 by budget) + prompt | ~1.5k tokens ≈ $0.0003–0.0005 |
| Jev | $0.042 × in_M | 1.5k tokens ≈ $0.00006 |
| Embeddings | $0.01 × in_M (DeepInfra) | 1M tokens = $0.01 |

### What a task costs now [trace]
Baseline: **$0.026–0.033 per task**, ~19 Gemma calls at ~7.5k input tokens each; L3 tasks use only ~25 % more
steps than L1. Rehearsal kit finals: $0.66–0.81 per run ($0.014–0.017 per task).

### Budget envelopes for 48 tasks (16 per level) [derived]
| Target run cost | Per task | ≈ Gemma calls/task at 3k-token context | Suggested split L1 / L2 / L3 |
|---|---|---|---|
| $3.00 (hard limit; 403 after) | $0.0625 | ~90 | never plan for this |
| $1.00 | $0.021 | ~30 | $0.010 / $0.020 / $0.032 |
| **$0.50 (suggested target)** | $0.010 | ~15 | $0.005 / $0.010 / $0.016 |
| $0.25 | $0.005 | ~7 | $0.003 / $0.005 / $0.008 |

Rules of thumb:
- Keep a reserve: stop new spending at ~$2.5 measured (local tally of `usage.cost`; OpenRouter usage lags seconds),
  and always submit a best answer before risking more calls.
- Everything other than Gemma is rounding error: 1,000 Jev calls at 1.5k tokens = $0.06; embedding the prose of the
  workspace ≈ $0.01–0.02.
- Biggest wins: (1) carried context per step (8k → 3k cuts cost ~60 %), (2) steps per task (19 → 8), (3) no
  thinking on routine steps. Deterministic tools/parsing replace whole loops for logs and tables.

---

## Open questions / unverified
- How OpenRouter maps `reasoning.effort` levels for Gemma (on/off only, or a budget?). Measure tokens per effort on
  ~6 tasks.
- Whether `dimensions` is honoured for qwen3-embedding on OpenRouter (no `supported_parameters` listed).
- Whether the gateway forwards `GET /api/v1/key` (live budget checks).
- Jev image questions (ticket stamps): documented as early; not tested here.
- Exact Gemma image token cost on DeepInfra (which visual budget the provider uses) — read `usage.prompt_tokens` on
  one `view_image` call.

## Sources
- OpenRouter endpoints API: <https://openrouter.ai/api/v1/models/google/gemma-4-31b-it/endpoints>,
  <https://openrouter.ai/api/v1/models/typesafe/jev-1.14/endpoints>,
  <https://openrouter.ai/api/v1/models/qwen/qwen3-embedding-8b/endpoints> (all fetched 2026-11-17)
- OpenRouter model page: <https://openrouter.ai/google/gemma-4-31b-it>
- Gemma 4 model card: <https://gemma.ai/models/gemma-4/model-card/> (redirect from ai.google.dev/gemma/docs/core/model_card_4)
- OpenRouter reasoning tokens: <https://openrouter.ai/docs/guides/best-practices/reasoning-tokens.md>
- OpenRouter limits: <https://openrouter.ai/docs/api/reference/limits.md>
- OpenRouter embeddings: <https://openrouter.ai/docs/api/reference/embeddings.md>
- OpenRouter Jev guide: <https://openrouter.ai/docs/guides/community/jev.md>; tutorial
  <https://openrouter.ai/docs/guides/community/jev-tutorial.md>; Decisions API reference
  <https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request.md>
- OpenRouter "Jev vs LLM": <https://openrouter.ai/blog/tutorials/jev-vs-llm-when-to-use-each/>; Jev-verified cascade
  cookbook <https://openrouter.ai/docs/cookbook/evaluate-and-optimize/jev-verified-cascade.md>
- TypeSafe docs: <https://docs.typesafe.ai/models.md>, <https://docs.typesafe.ai/primitives>,
  <https://docs.typesafe.ai/confidence>, <https://docs.typesafe.ai/concepts/system-one.md>
- Qwen3-Embedding-8B model card: <https://huggingface.co/Qwen/Qwen3-Embedding-8B>
- BEIR: Thakur et al., 2021, <https://arxiv.org/abs/2104.08663>
- Local: `README.md` (Models, Tips), `docs.md` (workspace, tools, scoring), `FINAL_RUN.md` (limits, latency),
  `arena/llm.py`, `agent.py`, `slides/AIM_Hackathon_Efficient_Local_Agents.pptx` (slides 18, 21, 23, 29)

## Method (trace statistics)
Python over `runs/*/t*.jsonl` (main checkout, git-ignored): per `assistant` event — latency = time since the
previous event, `finish`, presence of `reasoning`/`content`/`tool_calls`, `json.loads` on each call's `arguments`;
per run — max `cost_total` (the shared tracker's cumulative total) / tasks; steps by level joined from
`harvester/replay_results/*/*.json`. `cost_total` is shared across 4 parallel tasks, so per-task cost is only the run
average. Runs: `rp001_run_bV0bxqQTiZRa`, `rp002_run_Wxvjw3WImrpJ`, `rp002_run_aF3iOYCGwFiX`,
`rp003_run_aF3iOYCGwFiX`, `rp003_run_bV0bxqQTiZRa`, `run_iLM_ts7nIe2j`, `run_lI_N_uYzqRyP`, `rp001_run_CrCkPblWSN-Q`
(6-task smoke).
