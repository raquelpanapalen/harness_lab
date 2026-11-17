# Map: New harness for Harness Arena

Label: `wayfinder:map`. Tracker: local markdown (this folder). One file per ticket: `NN-<slug>.md`; `Status:` open/closed,
`Assignee:` is the claim, `Blocked by:` lists ticket files. Frontier = open, unblocked, unassigned tickets.

## Destination

A build-ready spec for the new harness: the architecture, the strategy for each kind of task, and how it's measured.
It replaces the kit baseline in `agent.py` and is ready to implement ticket by ticket. Building it happens after this map.

## Notes

- Domain: Harness Arena Task API (`docs.md`), offline replay + calibrated grader (`harvester/`), rules in `AGENTS.md`.
  Vocabulary in `GLOSSARY.md`.
- Final-legal throughout: only `google/gemma-4-31b-it`, `typesafe/jev-1.14`, `qwen/qwen3-embedding-8b` via OpenRouter;
  ≤ $3 and ≤ 30 min per run; runs in the container. No local models.
- Goal: maximise score on held-out replay runs. Cost and time are hard limits, not things to minimise.
- Reuse `arena/` (client, llm, cost tracking) and `main.py`; replace `agent.py`. Python.
- Measurement: 39 older harvested runs for development; the 5 newest are held out (`--exclude-runs 5` / `--only-runs 5`:
  run_CrCkPblWSN-Q, run_bV0bxqQTiZRa, run_aF3iOYCGwFiX, run_Wxvjw3WImrpJ, run_r1mftr6lxlr3). Never read held-out
  traces or tasks for design; look only at their scores, at milestones. Judge decisions on whole runs (2 repeats where it
  matters): per-task noise is 0.12–0.24.
- Dev budget: hard cap $40 OpenRouter, aim well under $20 (other teams spent about $6 total). Research makes no model calls.
- The harness never reads `harvester/data/` (expected answers). Research may read it to learn task structure; findings
  must not turn into memorised answers.
- Sessions consult skills: grilling + domain-modeling for grilling tickets, research for research tickets.

## Decisions so far

<!-- one line per closed ticket -->

- [Baseline failure analysis](01-baseline-failure-analysis.md): two thirds of lost points are provider errors (429 rate limit, 400 above ~8k-token context) that the kit never retries; then wrong content (12.6), format (3.4), citations to the wrong file (2.9); fixes to retries + small context alone ≈ 0.26 → 0.49
- [Pipeline architecture](06-pipeline-architecture.md): setup → intake (Jev over family descriptions) → family solver or notebook loop (≤ ~8k-token prompts, no history re-sent) → compose (structured JSON incl. actions) → code check → always submit; own thin code on `arena/`
- [Prior art for small-model agents](05-prior-art.md): guards in code first, per-family workflows with the model for judgment, locator-tagged BM25 index, code-grounded verification, plan-then-execute; ≈ 15k–40k tokens per task is competitive
- [Model capabilities and costs](04-model-capabilities.md): Gemma cost is re-sent context (cut context and steps); syntax reliable, judgment not; Jev for cheap typed yes/no/choice screening, not counting; embeddings only for prose, with BM25
- [Task taxonomy and what code can solve](02-task-taxonomy.md): 23 regular families, ≈ 75 % computable by code from cached per-run facts; model for instruction-likeness judgments, claim parsing and images; refusals are rule-driven; 650k–890k tokens/task, mostly noise
- [Injection landscape](03-injection-landscape.md): team attacks mostly frame clean agents or plant URLs, ~75 % in audit `detail`; scenario traps are evidence, so tag, never delete; output guards + a frame guard (agent ids must be backed by events) beat input filtering
- [Workspace index and retrieval](07-retrieval.md): download each run's blobs once; index evidence units (exact grader locators) cited by handle; tables in SQLite for code solvers; local BM25 (embeddings only if measured); local read-only tools; isolation by blob hash; `untrusted` flag per unit
- [Image inputs](12-vision.md): transcribe every distinct image once per run (≈ $0.05), one generic verbatim-plus-marks prompt, no vision in the loop; measure accuracy on dev images before adding second reads; transcripts are untrusted text; no Jev on images

Charting decisions (made with the human while naming the destination; not tickets):
- Shape: hybrid. Code does the preparation (workspace index, task classification, allowed actions); one model loop works
  on top; family-specific helpers only where measured to pay.
- Always submit: every task gets a best-effort submission or a fallback outcome, whatever fails.
- Safety in code: allowed state-changing actions derived from the instruction and enforced before the API call; file
  content passed to the model as quoted data.

## Not yet specified

- Prompt design per stage, and how the system prompt frames untrusted content.
- Citation strategy: which evidence units to cite (handles make locators exact; the choice and cap are still open).
- Implementation plan: ticket slicing of the spec once it exists.

## Out of scope

- A final or demo submission (event over; none planned).
- Writing attacks for other teams (`/payloads`).
- New harvests or live arena runs, except to re-check the grader.
