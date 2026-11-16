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
