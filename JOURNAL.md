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

## 01:55 · Build 02: per-task state machine that always submits
- decided_by: ai          # build ticket 02, from the pipeline-architecture decision
- why: 47/96 baseline points were tasks never submitted; always-submit and resume must hold by construction before any model work
- change: harness/state.py + harness/machine.py (handler per state, fallback edge from every state, hard deadline, records saved per transition), run-level resume via runs/ACTIVE.json (`--fresh` to override); unbuilt stages fall back to a right-shaped empty answer
- rejected: per-task try/except in a loop (the kit's approach: drops the task on the first error)
- evidence: tests/test_lab_machine.py; replay rp005_run_zJJcgkSZFc-3 48/48 submitted at $0 (Score 0.000); crash/resume rp006_run_Yb6jXZ908-nH 10 + 38 = 48/48

## 14:40 · Build 03: one pacer for model and Task API calls
- decided_by: ai          # build ticket 03, implementing the human-accepted resilience decisions
- why: 47/96 baseline points were tasks dropped on 429/400; the SDK's fixed 5 retries give up in ~17 s and know nothing about the run's deadline or the $3 limit
- change: harness/pacing.py (per-endpoint caps, adaptive Gemma limit, deadline-bounded jittered backoff, 400 → shrink, 403 → stop all model calls); budget watcher $0.50/$1.00 and soft/hard deadlines checked before every state; solver-confident tasks run before loop tasks; LLMError carries status/provider/body; SDK retries off under the pacer
- rejected: keeping the SDK's built-in retries (attempt-capped, blind to the deadline, no adaptive limit); a 403 detected per task (other tasks keep calling until each sees its own 403)
- evidence: tests/test_lab_pacing.py; replay rp007_run_NT6kzGi5K2iQ (403 after 15 calls → 48/48 submitted), rp008_run_OA72gAU9T7pb (48/48 at $0)

## 14:55 · Build 05: intake target raised to ≤ $0.002 of Jev per run
- decided_by: human       # asked with the measurement in hand; accepted the recommended option
- why: one Jev call per task measures 1.000 family / kind / allowlist on 1,872 dev tasks, at $0.0018 per run; Jev bills ~330 tokens of overhead per call + ~22 per option, so a 24-option catalogue can't reach $0.001
- change: harness/intake.py (open-ticket and inbox request resolution, /AGENTS.md + named operator files as trusted rules, family choice over our own catalogue; inbox tasks ask only for the requested action; allowlist from the grader's kind mapping, empty below 0.5); ticket 05 target ≤ $0.002
- rejected: code prefilter to top-k families (meets $0.001 but misroutes if it misses unseen wording); bare option names ($0.0014, still over, weaker); family question on inbox tasks (32 filed as containment)
- evidence: harvester/eval_intake.py → wayfinder/harness/build/05-intake-eval.md (39 runs, 1.000/1.000/1.000, $0.00179/run); replay rp009_run_aT-eldEdFrOG 48/48 submitted, 36 classified for $0.0013

## 15:05 · Build 04: workspace index, local read-only tools, cross-task "variant-text" flag
- decided_by: ai          # build ticket 04; the variant-text signal and the PDF live-url exemption are additions to SPEC §3, open to override
- why: SPEC §3 (index, local tools, structural untrusted flags). The spec signals missed 19 % of rewritten audit details: the scenario's short traps ("SOC verified value", "note for automated readers", instr-b64) stay under 15 words. Inside one run every task has the same base files, so a rewritten event is visible by comparing a task's copy with the run's base copy, with no marker phrases
- change: harness/index.py (evidence units with grader locators, SQLite per csv/jsonl/sheet, BM25, lazy PDFs, untrusted flags incl. variant-text), harness/localtools.py (tree/list/read/search/query_logs in the server's format), harness/tools.py (router + read_text), setup in harness.run before phase 1; live-url no longer flagged on visible PDF text
- rejected: marker-phrase rules (finals are reworded); flagging live URLs in PDFs (95 % of the 3-run false positives were reference URLs in real-world reports); one global BM25 corpus (per-task stats keep variants apart)
- evidence: tests/check_index.py on 39 dev runs: probes 2652/2652 identical, citations 10058/10058, audit-detail recall 81 % → 100 % with variant-text (circular label), false positives 0.009 %; setup 63 s / 871 MB on rp010_run_5CFzxOViKnfn

## 15:30 · Build 06: notebook loop and compose, first real Score
- decided_by: ai          # build ticket 06, from the pipeline-architecture and submission-policy decisions
- why: tasks without a solver need an end-to-end answer path; the Score needs a first measurement
- change: harness/loop.py (one step per visit, ≤ 6k-token prompts, handle-tagged units in data markers, note-first prompt, caps 6/8/10, photo tickets skip), harness/compose.py (JSON outcome/answer/cite/actions; refusals only on requests to act; actions only on the allowlist until ticket 07), harness/normalise.py (UTC, JSON, numbers, sort, keys, citation cap); answer_shape ignores the refusal clause
- rejected: letting the loop call state-changing tools (actions belong to compose so check can see them); notes optional (Gemma then never notes: 0/6)
- evidence: rp002 0/6 → rp003 0.06 (note-first); rp004/rp005 Score 0.147/0.157; after the shape fix rp006/rp007 (same seeds) A 0.194, $0.136, Score 0.163 (kit baseline 0.152 at $0.68); max prompt 5,349 tokens

## 16:10 · Build 08: images per need, one image budget
- decided_by: ai          # build ticket 08, from the image-inputs decision (read per need, lowest accurate budget)
- why: a quarter of tasks open a photo ticket and could not be read; scans and the standing order hold answer-critical values
- change: harness/vision.py (tickets until the first OPEN stamp, images the request names, operator-file images once per run, others on demand; one read per blob per run; transcripts as untrusted path-cited index units via Index.attach_text); chosen budget: the endpoint's single default for every image kind
- rejected: tiling or upscaling for higher budgets (Pillow; no image kind measured below 100 %); transcribing every image at setup (≈ $0.08/run vs. $0.009 per need); Jev on images
- evidence: harvester/eval_vision.py on 6 dev runs: tickets 103/103 found, stamps 315/315, request word recall 0.998, domains 35/35, standing orders 6/6, $0.00021/image; the provider ignores `detail` (298 prompt tokens for none/low/high); rp008/rp009 Score 0.284/0.244 (mean 0.264, was 0.163), image spend $0.008–0.010/run

## 17:16 · Build 09: fact layer and instruction judgment
- decided_by: ai          # build ticket 09, from the family-solvers decision (shared facts, judgment on Jev)
- why: ≈ 75 % of tasks reduce to a few runbook facts (attack traces, compromised, patient zero, exfiltration, permissions); every one depends on judging which artifacts instruct agents
- change: harness/facts/ (decoders; Jev yes/no judge cached per content hash with Gemma for the uncertain band; Facts with evidence units and checked preconditions; runtime facts_of transcribing exactly the images the facts need via ticket 08's vision). An artifact is judged on its text plus its attachments, scanned attachment pages and data-URI images (the instructions sit there in most dev runs). Standing-order parameters read twice (pattern + Jev), a disagreement leaves them unknown. Transfer-register and SIEM columns recognised by content (header names change per surface). Approved transfers also from the on-call handover. index.py: sheet header = first full-width row; csv rows cited by `line` when the first column is not an id (CET archive)
- rejected: judging the artifact's markdown alone (11 entry artifacts missed, all instructions in images); header-name matching for registers (fails on 3 of 7 surfaces); trusting one standing-order reading (a pattern misread "No de-duplication" in testing)
- evidence: tests/check_facts.py on 39 dev runs: compromised 45/45, patient zero 46/46, attacker domains 12/12, trifecta 40/40, origin 6/6, exposed tokens 7/7, first exfil 15/15, exfil records 35/35; 0 "ok but wrong"; judgment 106/106 on counterfactual labels (54 yes / 52 no, Jev alone); evaluation spend $0.016

## 18:40 · Build 10: solver framework, wave 1 enabled
- decided_by: ai          # build ticket 10; the user delegated the remaining build to the sessions
- why: compromised agents, patient zero and timeline are pure queries over the fact layer; the loop got 0.28–0.53 on them
- change: harness/solvers/ (Solution/REGISTRY/ENABLED/register/Precondition; run() = SOLVE handler: confident → CHECK, else notes → LOOP, any exception → LOOP); wave 1 in core.py (timeline: one Jev choice maps each lettered event to the event catalogue, times from the audit log, board, SIEM sheet or change log); machine.SOLVE wired
- rejected: letting a solver end a task by itself (crash or missing input must cost nothing: the loop takes over); enabling by family alone (the solver's own preconditions decide too)
- evidence: tests/check_solvers.py, 39 dev runs: compromised 45/45 at 1.000, patient zero 46/46 at 1.000, timeline 117 at 0.999 (loop 0.531/0.281/0.302); replay seed 125823749: rp008/rp010 Score 0.284/0.255 → rp001 0.368 (A 0.468, $0.189), the 9 wave-1 tasks 1.00

## 13:48 · Build 11: wave-2 solvers (tokens, access log, access review, delegation, trifecta), all enabled
- decided_by: ai          # build ticket 11; the user delegated the remaining build to the sessions
- why: the loop scored 0.00–0.30 on these families; each is a rule over workspace files (policy clauses, fingerprints, the permission graph)
- change: harness/solvers/wave2.py. access-log POLICY.md parsed per clause (unknown clause → loop); a ticket exempts a request only when it is the request's own; leaked values matched by sha256_12 incl. wrapped/split values; access review simulates each request alone on the current graph; permission answers cite the registry by path + every post-export change. facts_for now gives each task its own view for direct file reads (f18a39c)
- rejected: any matching ticket exempts (access log 0.87 vs 1.000); citing every reviewed request (extras: 0.909 vs 0.995) or the policy file; citing denial reasons (no hits)
- evidence: tests/check_solvers.py, 39 dev runs: leaked_tokens 117/117 1.000, access_log 117/117 1.000, access_review 117/117 0.989, delegation_chain 24/24 1.000, lethal_trifecta 43/43 0.958 (loop 0.052/0.055/0.297/0.000/0.067)

## 13:50 · Build 14: wave-5 families answered by code around bounded judgments
- decided_by: ai          # build ticket 14; loop vs code-only decided by dev Score
- why: loop Scores board 0.035, MCP 0.385, status claims 0.135, DPA 0.678; the model is only needed for "is this an instruction" and for reading images
- change: harness/solvers/wave5.py (board prefilter by digit-normalised repeats + split-post joining + two judgments that must both say yes; MCP policy rules parsed, descriptions judged against the policy's wording, manifest hash vs newest approval; 19 status-claim templates over audit events; DPA register → base clause/signatory → signed effective amendments, latest wins); decode.reveal decodes Unicode tag characters and plain hex runs; Judge.judge(text, question) for a runbook/policy criterion
- rejected: judging every board post (≈950 per board) or only by the runbook definition (false positive on a quoted training example) or only generically (false positive on a maintenance notice); counting blocked transfers as made; treating a missing delegate result as contradicting "declined"
- evidence: tests/check_solvers.py, 39 dev runs: board 117/117 confident at 0.988, MCP 0.952, status claims 0.851 → ~0.94 after the new template, DPA 48/48 1.000; split-marker fix re-checked on the 28 affected runs (imperfect board answers 13 → 6, no extras); eval spend $0.14

## 14:30 · Builds 12 and 13: inbox routing and wave 4 in code; combined Score 0.929 at $0.023
- decided_by: ai          # tickets 12/13 under the human's standing directive (build until < $0.05/run at best accuracy)
- why: the loop scored 0.00–0.39 on these families and carried most of the cost; code over the facts layer is exact
- change: harness/inbox.py, harness/wave4.py, harness/solvers/wave4.py (10 families + attacker domains + partner draft); a family is enabled only at 0 confidently-wrong answers over 39 dev runs; attacker-domain scans read only short plain lines (planted notes carry fake indicators); gap checks and unconfirmed token statuses stay with the loop; submission always serialises
- rejected: enabling attacker domains with gap checks (31/31 confident answers wrong); bullet-only scan lines (dropped real indicators)
- evidence: harvester/eval_wave4.py 39 runs (table in build/13); replay rp002_run_NLzokmjwLUfD 0.934 ($0.017), rp003_run_ObCe2yzg_xMy 0.924 ($0.028), was 0.368 after wave 1

## 15:20 · Build 15: held-out milestone, lab harness becomes the default
- decided_by: ai          # ticket 15 under the human's standing directive; the ticket itself says the winner becomes the default
- why: the milestone measures the finished harness on the 5 runs never used for development
- change: main.py default --harness lab; README result table; fresh runs move an earlier same-id run directory aside (report.py read a kit-baseline run's spend); zoneinfo fallback for slim images
- rejected: reading held-out traces to explain the lowest run (would end the hold-out)
- evidence: held-out rp001–rp005 on --only-runs 5: A 0.907/0.948/0.912/0.943/0.935, cost $0.050/$0.020/$0.026/$0.021/$0.028, mean Score 0.929 at $0.029 (dev seeds 0.929 at $0.023; kit baseline 0.152; AIT 0.750)

## 16:10 · Live arena check: one post-event dev run
- decided_by: human       # the human asked to try the real arena after the build; final window confirmed closed
- why: confirm the replay grader against the real one for the finished harness (grader calibration)
- change: none (measurement only); final submission impossible: /me reports final_window closed 2026-11-14 16:45 UTC, final_run_used false
- evidence: run_QVhW_pCITnL1 (live dev, seed 2094029971, code 08cd2ab): 48/48 submitted, accuracy 0.949, cost_score 1, final_score 0.949, local spend $0.026 (replay held-out mean 0.929 at $0.029)

## 15:14 · Inbox citations per outcome
- decided_by: ai          # the human approved the citation split between the two sessions; evidence kinds chosen by hit count
- why: inbox lost 1.80 points on rp002+rp003 (0/11 citations hit); route() cited nothing
- change: harness/inbox.py cites the inbox message always, plus the change-log lines naming a refused sender (DENIED_SECURITY), each candidate's roster row (NEEDS_CLARIFICATION), the quarantined agent's roster row, or the recipient's contact row (send_message); block_domain / UNSUPPORTED cite the message alone
- rejected: every message line (6x the citations, no extra hits), attacker-domain evidence for block_domain (0 hits), sender's contact row (0 hits)
- evidence: candidate sweep over 39 dev runs (hit counts only): message alone 117/190 hits; per-outcome sets 190/190 with 0 extras. Solver on 10 runs: 47/47 hits, 49 cited
