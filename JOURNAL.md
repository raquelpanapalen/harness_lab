# Journal

One entry per design decision, newest last: whenever someone on the team makes or overrides a design call, and when
the coding agent makes one. Mark `decided_by` honestly; the content isn't scored. The journal feeds your 18:00 story
page, and your upload needs at least one entry of your own to be eligible for prizes.

Format example (ignored: it sits in a code block). Write your entries below it, outside the block:

```markdown
## 11:42 · Jev pre-filter for long documents
- decided_by: human        # human | ai | both
- why: t07 L3 at 0.31; reading every document with the main model costs $0.04/task
- rejected: regex filter (misses base64), main model only (cost)
- evidence: run_abc → run_def, t07 0.31 → 0.78, cost −40 %
```

## 21:37 · Offline dataset + local replay server for evaluation
- decided_by: human
- why: post-event, so iterate offline instead of on live dev runs; harvested dev runs give tasks, workspaces and revealed answers at $0 model cost
- rejected: only the kit's 2-task mock (too small to measure anything); building a scenario generator (can't match theirs)
- evidence: 13 harvested runs (624 tasks, theme hub); replay `tree` byte-identical on 528 recorded tasks; oracle scores 1.0 on all 528 tasks of the first 11 runs; threat_intel backfilled via seed replay (15/15 spot-checked verdicts identical)

## 17:56 · Go fully local: 44-run dataset, replay matched to real tool outputs and grader
- decided_by: human
- why: no final submission planned; iterate offline on the replay instead of live dev runs
- rejected: more live dev runs per experiment (other teams' attacks still in the pool make them noisy; e.g. injected_action:t18-p4 in calibration run_Crk_0XtUy41q)
- evidence: 44 runs / 2112 tasks; 1269/1269 recorded real tool responses identical in replay; grader fitted on 249 real graded variants (246 agree); calibration runs run_Crk_0XtUy41q, run_icytTabjDbdw, run_DODDwIETh7Rd, run_UzP-XT7I2hbJ, run_YyvYOlNqL5IT

## 19:45 · Baseline on held-out replay runs; retry empty model responses
- decided_by: ai
- why: kit baseline on the 3 held-out replay runs scored 0.310 / 0.351 / 0.379 (mean 0.347, $1.26–1.55 per run); 12 of 144 tasks were never submitted because OpenRouter returned 200 with no choices in a short window, and `llm.chat` gave up at once
- change: `llm.chat` retries an empty response up to 4 times with backoff; replay server numbers runs under its lock (two parallel runs both got `rp003`)
- rejected: retrying in `agent.py` (every caller of `chat` needs it)
- evidence: replay rp002_run_Wxvjw3WImrpJ 0.351, rp003_run_aF3iOYCGwFiX 0.379, rp003_run_bV0bxqQTiZRa 0.310; weakest: logs/audit 0.24, notify 0.00, sources 0.25 overall

## 21:10 · Replay checked against real harness runs; grader and action tools fixed
- decided_by: both          # human asked for the real-vs-replay comparison and the three fixes; fixes designed by ai
- why: the kit baseline on 2 real dev runs (same seeds as held-out replay runs) showed the local grader agreed on only 58/82 organic submissions: citations were matched by locator only, sloppy answer formats scored 0, other teams' planted framings and the real authorisation/freeze rules were missing, and the state-changing tools' formats were guesses
- change: citations matched by the evidence they point at (record in a csv row, request id across files, agent + trace in the audit log; path-only citations of one file count once); list answers given as strings split; numbers as strings; per-task authorisation of state-changing calls refused at call time; rotation_in_freeze; injected_action for answers naming agents framed by planted text; containment/send_message responses identical to real; new `rotation` task kind (42 tasks)
- rejected: an LLM judge for injected framings (cost, and a regex on claim language already finds 8/8 with 0/29 false alarms); per-run caching of framings (each task has its own injections)
- evidence: real run_lI_N_uYzqRyP 0.311 vs replay 0.294 / 0.288; real run_iLM_ts7nIe2j 0.315 vs replay 0.402 / 0.413; full grade incl. constraints identical on 96/96 real tasks, calibration 239/242, state-changing responses 12/12, read-only probes 1269/1269, oracle 1.0 on 2112 tasks

## 22:40 · New harness: workspace index of citable evidence units, cited by handle
- decided_by: ai          # proposed in the wayfinder retrieval ticket; human accepted every recommendation
- why: baseline citation score 0.25–0.28 and ~19 calls/task re-sending context; 75 % of tasks are joins/counts over csv/xlsx/log data (research/task-taxonomy); ids dominate, so embeddings add latency for little gain (research/model-capabilities)
- change (spec, not built yet): download each run's ~950 blobs once; index evidence units with exact grader locators; model cites handles, code writes `sources`; tables in SQLite for code solvers; local BM25; read-only tools served locally; isolation by blob hash; `untrusted` flag per unit
- rejected: browsing with server tools (cost, garbled locators); embeddings in v1 (≈ 8 s/request under load, ids dominate); raw SQL for the model (small-model join errors)
- evidence: design only; wayfinder/harness/07-retrieval.md

## 22:55 · New harness: transcribe every image once per run
- decided_by: ai          # proposed in the wayfinder image-inputs ticket; human accepted every recommendation
- why: 71–106 distinct images per run (ticket photos, signed standing order, scans, amendments); baseline re-reads them with 56–72 `view_image` calls per run; reading all once costs ≈ $0.03–0.05
- change (spec, not built yet): one generic verbatim-plus-marks transcription per distinct image at run start, cached by hash; transcripts are untrusted text; accuracy measured on dev images before adding second reads or higher resolution
- rejected: per-image-type prompts (depend on dev file names); Jev for stamp checks (image support early, long-context mode); vision inside the agent loop (repeated cost)
- evidence: design only; wayfinder/harness/12-vision.md

## 23:20 · New harness: pipeline of setup, intake, solver or notebook loop, compose, check, submit
- decided_by: ai          # proposed in the wayfinder pipeline-architecture ticket; human accepted every recommendation
- why: baseline lost 47/96 points to provider errors (400s start above ~8k-token prompts) and most of the rest to wrong content/format; successful tasks need ~8 steps and ~42k tokens, failures ~140k at the step cap; ≈ 75 % of tasks are computable by code
- change (spec, not built yet): run setup (index, SQLite, image transcripts) → intake via a Jev choice over family descriptions → family solver, else a notebook loop with ≤ ~8k-token prompts and no re-sent history → compose writes structured JSON incl. state-changing actions → code checks (shape, citations, framed agents, URLs, authorisation) → always submit; own thin Python on `arena/`
- rejected: agent framework (pydantic-ai: loop is the smallest part; guards and context control need our own code); regex routing on dev wording (the arena rewords tasks); growing chat history (cost and 400s); state-changing calls inside the loop (unguarded)
- evidence: design only; wayfinder/harness/06-pipeline-architecture.md; research/baseline-failures

## 23:40 · New harness: deterministic action guard and frame guard
- decided_by: ai          # proposed in the wayfinder action-guard ticket; human accepted every recommendation
- why: any constraint violation zeroes a task; baseline lost 4 tasks to them (2 quarantines alongside NEEDS_CLARIFICATION, 1 framing, 1 freeze); team attacks are 66 % framings, 26 % live URLs, ~75 % in audit `detail` (research/injection-landscape)
- change (spec, not built yet): explicit per-task allowlist emptied on refusals; code-validated action arguments (roster, stand-ins, scheduler, verified recipients, named paths); agent ids need trusted backing; outgoing text defanged and stripped of secrets and rules text; workspace /AGENTS.md the only trusted text; one repair round, then submit without the blocked part
- rejected: deleting instruction-like text (some is the evidence asked about); Jev or model screening inside the guard (can be talked round, refusing on detection hurts utility); refusing instead of rewriting
- evidence: design only; wayfinder/harness/08-action-guard.md

## 23:50 · New harness: each task is an explicit state machine
- decided_by: both        # human asked whether it would be a state machine; ai proposed the shape
- why: 47/96 baseline points were tasks dropped on provider errors; always-submit and resume must hold by construction
- change (spec, not built yet): enum states INTAKE → SOLVE/LOOP → COMPOSE → CHECK → REPAIR (once) → SUBMIT, a FALLBACK_SUBMIT edge from every state, task record saved per transition, deadline/budget watcher
- rejected: LangGraph or another graph framework (9 states, fixed edges)
- evidence: design only; wayfinder/harness/06-pipeline-architecture.md (addendum)

## 00:10 · New harness: family solvers over a shared per-run fact layer
- decided_by: ai          # proposed in the wayfinder family-solvers ticket; human accepted every recommendation
- why: ≈ 75 % of tasks are computable from a few per-run facts (research/task-taxonomy); wrong answer content is the largest non-infrastructure loss (12.6 of 96 points, research/baseline-failures); big-file families scored 0 on 19 tasks
- change (spec, not built yet): fact layer per run and per audit/board version; solvers implement runbook concepts with values read from the workspace; Jev yes/no (after decoding) for "instruction to agents?"; two-part recognition; solvers enabled only where they beat the loop on dev; helpers for model-heavy families; scenario assumptions as checked preconditions
- rejected: regex routing on dev wording; solvers for model-heavy families; hard-coded values from dev runs (memorisation, AGENTS.md)
- evidence: design only; wayfinder/harness/11-family-solvers.md

## 00:20 · New harness: submission normalisation, verified refusals, citation cap, fallback ladder
- decided_by: ai          # proposed in the wayfinder submission-policy ticket; human accepted every recommendation
- why: baseline lost 3.4 points to answer format, 2 to wrong outcomes; 61/95 citation misses cited the wrong file (often the carrier ticket); a wrong or empty answer scores the same 0 as none
- change (spec, not built yet): schema from the instruction + code normalisation; refusals need a code-verified reason; cite answer evidence only, cap items + 2; fallback ladder solver → notebook compose → right-shaped empty OK
- rejected: refusal as a fallback (zeroes OK tasks); citing "just in case" (penalty past expected + 2)
- evidence: design only; wayfinder/harness/10-submission-policy.md

## 00:35 · New harness: pacing, retries and deadlines
- decided_by: ai          # proposed in the wayfinder resilience ticket; human accepted every recommendation
- why: 47/96 baseline points were tasks dropped on 429/400 (research/baseline-failures); FINAL_RUN.md: 30 min hard stop, 403 at $3, Jev slows above 10 parallel calls
- change (spec, not built yet): per-endpoint concurrency caps with adaptive Gemma limit; deadline-bounded backoff; 400 → shrink context and log the provider; 403 → fallback-submit all; solver tasks first; soft/hard deadlines 24/27 min; resumable task records; sequential dev runs
- rejected: the kit's fixed 5-retry cap (gives up after ~17 s and drops the task); parallel dev runs on one key (caused the 429s)
- evidence: design only; wayfinder/harness/13-resilience.md

## 00:55 · New harness: optimise the leaderboard Score, ≤ $0.10 per run
- decided_by: human       # human pointed at the leaderboard and asked to be comparable on score and cost
- why: leaderboard Score = A·(0.5+0.5·C), C log-scaled $0.049 → 1 to $1.135 → 0; AIT won at A 0.751 for $0.049 (0.750) over Donaufriends A 0.765 for $0.354 (0.524); at A 0.75, $0.50/run caps Score at ≈ 0.47
- change (spec, not built yet): objective = Score on the event's fixed C range; target ≤ $0.10/run; loop 6/8/10 steps with ≤ 6k-token prompts; images read per need at low resolution; families where the loop scores ~0 submit code-only answers; runaway watcher at $0.50 / $1.00
- rejected: accuracy-only objective with $3 as a hard limit (not comparable with the board); $1/run soft target (Score ≈ 0.40 at best)
- evidence: arena.ai-mission.eu/leaderboard read 2026-11-17; wayfinder/harness/09-model-roles-budget.md

## 01:30 · Build 01: harness selectable in main.py, parsers shared
- decided_by: ai          # build ticket 01 (prefactor), sliced with the human
- why: the new harness must run beside the kit baseline (reference for comparisons), and its index reuses the document parsers
- change: `main.py --harness baseline|lab` (baseline default); new `harness/` package with a stub entry point; parsers moved from agent.py to arena/parse.py
- rejected: replacing agent.py in place (loses the reference baseline)
- evidence: parser parity 46/46 files identical; replay smoke runs rp003_run_NLzokmjwLUfD (lab stub, $0) and rp004_run_ObCe2yzg_xMy (baseline, 1 task, 1.00, $0.011)
