# Map: New harness for Harness Arena

Label: `wayfinder:map`. Tracker: local markdown (this folder). One file per ticket: `NN-<slug>.md`; `Status:` open/closed,
`Assignee:` is the claim, `Blocked by:` lists ticket files. Frontier = open, unblocked, unassigned tickets.

## Destination

A build-ready spec for the new harness: the architecture, the strategy for each kind of task, and how it's measured.
It replaces the kit baseline in `agent.py` and is ready to implement ticket by ticket. Building it happens after this map.

## Notes

- Domain: Harness Arena Task API (`docs/task-api.md`), offline replay + calibrated grader (`harvester/`), rules in `AGENTS.md`.
  Vocabulary in `docs/glossary.md`.
- Final-legal throughout: only `google/gemma-4-31b-it`, `typesafe/jev-1.14`, `qwen/qwen3-embedding-8b` via OpenRouter;
  ≤ $3 and ≤ 30 min per run; runs in the container. No local models.
- Goal: the leaderboard Score on held-out replay runs, Score = A · (0.5 + 0.5 · C), C log-scaled from 1 at $0.049 to
  0 at $1.135 per run (event range). Target ≤ $0.10/run. Bars: AIT 0.750, Donaufriends 0.524. (Was: accuracy with cost
  as a hard limit; changed in Model roles and budget per level.)
- Reuse `arena/` (client, llm, cost tracking) and `main.py`; replace `agent.py`. Python.
- Measurement: 39 older harvested runs for development; the 5 newest are held out (`--exclude-runs 5` / `--only-runs 5`:
  run_CrCkPblWSN-Q, run_bV0bxqQTiZRa, run_aF3iOYCGwFiX, run_Wxvjw3WImrpJ, run_r1mftr6lxlr3). Never read held-out
  traces or tasks for design; look only at their scores, at milestones. Judge decisions on whole runs (2 repeats where it
  matters): per-task noise is 0.12–0.24.
- Dev budget: hard cap $40 OpenRouter, aim well under $20 (other teams spent about $6 total). Research makes no model
  calls. Spent so far ≈ $11.6.
- The harness never reads `harvester/data/` (expected answers). Research may read it to learn task structure; findings
  must not turn into memorised answers.
- Sessions consult skills: grilling + domain-modeling for grilling tickets, research for research tickets.

## Decisions so far

<!-- one line per closed ticket -->

- [Baseline failure analysis](01-baseline-failure-analysis.md): two thirds of lost points are provider errors (429 rate limit, 400 above ~8k-token context) that the kit never retries; then wrong content (12.6), format (3.4), citations to the wrong file (2.9); fixes to retries + small context alone ≈ 0.26 → 0.49
- [Pipeline architecture](06-pipeline-architecture.md): setup → intake (Jev over family descriptions) → family solver or notebook loop (≤ ~8k-token prompts, no history re-sent) → compose (structured JSON incl. actions) → code check → always submit, run as an explicit per-task state machine with saved records and a fallback-submit edge everywhere; own thin code on `arena/`
- [Action guard and injection handling](08-action-guard.md): per-task allowlist of operations (emptied on any refusal), code-validated action arguments, frame guard (agent ids need trusted backing), outgoing text rewritten (defang, strip secrets/rules), workspace `/AGENTS.md` the only trusted text, no model in the guard
- [Family solvers: which, and how they fail safe](11-family-solvers.md): solvers implement runbook concepts with workspace-read values over a per-run fact layer; Jev (decode first) for "instruction to agents?"; five build waves; two-part recognition; a solver ships only if it beats the loop on dev; model-heavy families get helpers; scenario assumptions are checked preconditions
- [Submission and fallback policy](10-submission-policy.md): schema from the instruction plus code normalisation (UTC `…Z`, sorted, parsed); refusals only with a code-verified reason; cite evidence units, never the carrier ticket, cap items + 2; submit on check pass; fallback ladder ends in a right-shaped empty OK
- [Provider errors, retries and pacing](13-resilience.md): per-endpoint caps (Gemma ≤ 6, adaptive on 429), deadline-bounded backoff, 400 → shrink context, 403 → fallback-submit all; solver tasks first; soft/hard deadlines at 24/27 min; records resume after restart; dev runs sequential
- [Model roles and budget per level](09-model-roles-budget.md): objective is the leaderboard Score (cost on a log scale); target ≤ $0.10/run; Gemma only where solvers can't answer; loop 6/8/10 steps, ≤ 6k-token prompts; images read per need at low resolution; families where the loop scores ~0 submit code-only answers
- [Prior art for small-model agents](05-prior-art.md): guards in code first, per-family workflows with the model for judgment, locator-tagged BM25 index, code-grounded verification, plan-then-execute; ≈ 15k–40k tokens per task is competitive
- [Model capabilities and costs](04-model-capabilities.md): Gemma cost is re-sent context (cut context and steps); syntax reliable, judgment not; Jev for cheap typed yes/no/choice screening, not counting; embeddings only for prose, with BM25
- [Task taxonomy and what code can solve](02-task-taxonomy.md): 23 regular families, ≈ 75 % computable by code from cached per-run facts; model for instruction-likeness judgments, claim parsing and images; refusals are rule-driven; 650k–890k tokens/task, mostly noise
- [Injection landscape](03-injection-landscape.md): team attacks mostly frame clean agents or plant URLs, ~75 % in audit `detail`; scenario traps are evidence, so tag, never delete; output guards + a frame guard (agent ids must be backed by events) beat input filtering
- [Workspace index and retrieval](07-retrieval.md): download each run's blobs once; index evidence units (exact grader locators) cited by handle; tables in SQLite for code solvers; local BM25 (embeddings only if measured); local read-only tools; isolation by blob hash; `untrusted` flag per unit
- [Image inputs](12-vision.md): images transcribed per need (tickets until the first OPEN, standing order once, scans for scan tasks), cached, low resolution first, one generic verbatim-plus-marks prompt, no vision in the loop; measure accuracy on dev images before adding second reads; transcripts are untrusted text; no Jev on images

Charting decisions (made with the human while naming the destination; not tickets):
- Shape: hybrid. Code does the preparation (workspace index, task classification, allowed actions); one model loop works
  on top; family-specific helpers only where measured to pay.
- Always submit: every task gets a best-effort submission or a fallback outcome, whatever fails.
- Safety in code: allowed state-changing actions derived from the instruction and enforced before the API call; file
  content passed to the model as quoted data.

## Not yet specified

The destination is reached: [SPEC.md](SPEC.md) consolidates every decision. Left to the build:

- Prompt wording per stage: settled by measuring Score on development runs.

The hand-off is done: the spec is sliced into 15 build tickets, [build/README.md](build/README.md) (01–03 and 05 done
as of 2026-11-17). Ticket 16 (added 2026-11-21) wraps up: code cleanup, repo structure, docs, presentation, article.

## Out of scope

- A final or demo submission (event over; none planned).
- Writing attacks for other teams (`/payloads`).
- New harvests or live arena runs, except to re-check the grader.
