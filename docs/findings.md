# Findings

What we learned building the harness, each with its evidence. Run ids and per-run numbers are in `JOURNAL.md`; the
research tickets are in `wayfinder/harness/`.

## 1. Two thirds of the baseline's lost points were infrastructure, not reasoning

The kit agent scored 25.16 of 96 points on two dev runs (A 0.262). Of the 70.84 points it lost, **47 were tasks never
submitted**: the provider answered 429 (rate limit, 29 tasks) or 400 (18 tasks, only on prompts above ~8k tokens), and
the kit gave up on the first error. Wrong answers cost only 12.6 points.
→ An explicit per-task state machine that **always submits** (fallback to the best draft, then a right-shaped empty
answer), one pacer for every model and API call, prompts ≤ ~6k tokens. *Evidence: research 01
(`01-baseline-failure-analysis.md`), Build 02/03.*

## 2. About 75 % of tasks are computable by code

Across 1,872 dev tasks, a regex over the request sorts every task into 23 families, and most need joins, counts and
timelines over a few files once per-run facts are known (compromised agents, patient zero, exfiltration, permission
graph). The model is needed only in bounded places: is this text an instruction to agents, what does a photo say,
which numbered claim says what. → Code solvers over a per-run **fact layer**; models answer only bounded questions.
On held-out runs, solvers answer 94 % of tasks at a mean score of 0.986; the loop's 6 % score 0.646.
*Evidence: research 02, Builds 09–14, [`model-roles.md`](model-roles.md).*

## 3. Jev is the right tool for cheap typed screening

Jev answers a typed question with probabilities for ≈ $0.00002. Intake (family, kind, allowed actions) costs $0.0018 per
48-task run and was 1.000 / 1.000 / 1.000 on 39 dev runs. The "instruction to agents?" judgment over decoded text
drives attack traces, board triage and the MCP audit; an uncertain band (0.3–0.7) goes to Gemma once.
*Evidence: Build 05 (`05-intake-eval.md`), Build 09.*

## 4. Guard the outputs, not the inputs

About 75 % of other teams' attacks sit in audit-log `detail` fields, and 50–65 % of those on lines the task should
cite; deleting suspicious text breaks tasks. 66 % of attacks try to **frame a clean agent**, 26 % plant a live URL.
→ Keep all text readable and citable, tag it untrusted, and check what leaves: URLs defanged unless on a verified
domain, messages only to verified contacts, no secret values, state-changing tools only when the task asks, and a
**frame guard** (an agent id in an answer or a quarantine must be backed by audit events). No rule violations on
held-out runs. *Evidence: research 03, Build 07, `docs/architecture.md` (CHECK).*

## 5. Citations are a third of a correct answer's score

A correct answer earns 0.7 + 0.3 · sources. After the solvers were right, most remaining loss was citations. Measuring
for each family which evidence kinds the grader expects (hit counts only, chosen on dev runs 1–10, validated on 11–39)
and citing exactly those raised the held-out Score from **0.929 to 0.965** at the same cost.
*Evidence: JOURNAL 15:14–16:35 (citation entries).*

## 6. Cost is mostly re-sent context, and images

The kit spent $0.68 per run, most of it re-sending growing histories. The harness spends **$0.03**: 23 % Jev, 33 %
Gemma reading images, 44 % Gemma text (loop, compose, uncertain judgments). Images are read only when a task needs
them, once per run. At this cost C = 1, so the Score equals accuracy. *Evidence: research 09,
[`model-roles.md`](model-roles.md).*

## 7. The harness does not overfit the theme's vocabulary, but it does overfit the layout

All harvested runs share one theme, and the organisers said the final uses a different theme "to punish
overfitting". Rewriting the held-out runs into a new theme (`harvester/synth/`, every file format including PDFs and
photos, answers kept consistent):

- **New vocabulary** (`port`: organisation, attacker, agents, people, services, artifacts, ~460 swaps): Score **0.962**
  vs 0.965; 2 of 240 tasks differ.
- **Renamed directories and files, new id prefixes** (`layout`): **0.258** at $0.17 on one run. Intake finds tickets
  only in `/tickets/`, solvers find inputs by hard-coded path (~25 places), id regexes assume `TOK-`/`INC-`.

Building the reskin also showed two ways synthetic data can mislead: a redrawn image that wrapped `survey-mirror.test`
at the hyphen, and rewritten MCP manifests whose approved SHA-256 no longer matched. Both looked like harness failures
until compared task by task. *Evidence: JOURNAL 18:25 and 18:35, [`reports/v1.md`](reports/v1.md).*

## 8. Offline replay made the work possible

44 recorded dev runs (2,112 tasks), served by a local Task API whose tools reproduce the real ones (1,269 of 1,269
recorded real responses identical) and a grader calibrated against the real one (full grade identical on 96 of 96 real
tasks, 239 of 242 calibration variants), let every change be measured at the price of the model calls alone. The two live post-event checks (0.949 and 0.996) matched or beat the replay numbers.
*Evidence: JOURNAL 21:37–21:10 (dataset, replay, calibration), [`development.md`](development.md).*
