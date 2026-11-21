# Who does what: code, Jev, Gemma

The final allows three models: the fixed small chat model (**Gemma 4 31B**, `ARENA_MAIN_MODEL`), **Jev**
(`typesafe/jev-1.14`, typed yes/no and multiple-choice questions with probabilities, ≈ $0.00002 a call) and an
embedding model (not used). Everything else is code. The rule behind the split (`wayfinder/harness/09-model-roles-budget.md`,
`04-model-capabilities.md`): **code computes, models only judge or read**, and each model call is a bounded question
whose answer code can check.

## The split in numbers

Task share: who produced the answer, 240 held-out hub tasks (5 runs, `v1-hub-baseline`). Spend share: model spend per
run, 5 held-out runs (port reskin; same code paths, run logs and traces).

| Who answers | Tasks | Share | Mean task score |
|---|---|---|---|
| **Code solver** (models only for intake and bounded judgments) | 226 | 94 % | 0.986 |
| **Gemma notebook loop** + compose | 14 | 6 % | 0.646 |

| Where the money goes (per run, mean $0.031) | Spend | Share |
|---|---|---|
| Jev: intake (1 call per task) and "is this an instruction to agents?" judgments | $0.0072 | 23 % |
| Gemma vision: transcribing the images tasks need (tickets, standing order, screenshots, scans) | $0.0102 | 33 % |
| Gemma text: loop steps, compose, uncertain judgments, status-claim parsing | $0.0136 | 44 % |

Embeddings were not needed: the workspace index (BM25 + SQLite over evidence units) finds what the loop asks for, and
solvers know which files they read (`wayfinder/harness/07-retrieval.md`).

## Code

Code does everything that has a definite answer once the right rows are found.

| Step | Where | What |
|---|---|---|
| Workspace index | `harness/index.py` | downloads every file once per run, parses all formats (csv, jsonl, xlsx/ods, docx/odt, pdf text) into **evidence units** that are exactly the grader's citation locators; SQLite tables and BM25 over them |
| Read-only tools | `harness/localtools.py` | `tree`, `list`, `read`, `search`, `query_logs` answered locally, byte-identical to the server |
| Fact layer | `harness/facts/runbook.py` | runbook concepts computed once per run: compromised agents, patient zero, attack traces, exfiltration under the standing order's counting rules, permission graph, quarantines |
| Family solvers | `harness/solvers/*.py` | 23 families answered from the facts, each with preconditions; a solver that is not sure hands its partial facts to the loop as notes |
| Guards | `harness/guards.py` | before any submit: defang URLs, verified recipients only, no secret values, actions only when the task asks, rotation windows, stand-ins, frame guard (an agent id in an answer must be backed by events) |
| Normalisation | `harness/normalise.py` | UTC timestamps, JSON shapes, sorting, citation cap |

Worked example, **containment** (`solvers/response.py:containment`): the request names agents, tokens and domains or
asks for "every compromised agent". Code resolves names against the roster (`resolve_agent`; several matches →
NEEDS_CLARIFICATION with every candidate), takes compromised agents from the fact layer, schedules each rotation at the
first hour outside every freeze and buffer of the token's own and consumer services (`guards.schedule_rotation`), adds
stand-ins for critical agents and filters our own and verified partners' domains. No model call.

## Jev

Jev answers typed questions about a text, cheaply and with probabilities, so code can set thresholds.

- **Intake** (`harness/intake.py`): one Jev call per task picks the family (23 choices + other), the kind and, for
  inbox messages, the requested action. Below 0.5 confidence the task goes to the loop with no actions allowed.
  On 39 dev runs: family, kind and allowlist 1.000 / 1.000 / 1.000 at $0.0018 per run
  (`wayfinder/harness/build/05-intake-eval.md`).
- **"Is this an instruction to agents?"** (`harness/facts/instruction_judge.py`): one yes/no per artifact, board post
  or manifest description, after decoding (base64, zero-width and look-alike characters, split posts:
  `facts/decode.py`), cached by content hash. It decides which artifacts start attack traces, so it drives
  compromised agents, patient zero, board triage and the MCP audit. The judge never sees the task text.

Worked example, **board triage** (`solvers/judged.py:board_injected`): code drops routine posts (text that repeats with
only numbers changed), joins split posts ("1 of 2"), decodes each candidate, then asks Jev the general question and, when the runbook defines
"injected post", that definition as a second question; a post counts only when both say yes.

## Gemma

Gemma reads images and handles what no solver recognises.

- **Images** (`harness/vision.py`): transcribed only when a task needs them (the open ticket, the signed standing
  order, images the request names), once per run, cached by hash. The transcript is indexed like any file, so it can be
  searched and cited.
- **Uncertain judgments**: when Jev's probability falls between 0.3 and 0.7, Gemma decides once.
- **Notebook loop** (`harness/loop.py`): 6 / 8 / 10 steps by level, prompts ≤ ~6k tokens, notes first; tools are the
  local read-only tools. Then **compose** (`harness/compose.py`): one call that turns notes into
  `{outcome, answer, cite, actions}`, checked by the guards.
- **Status claims** (`solvers/judged.py:status_claims`): parsing a report's numbered claims into checkable statements.

Where the loop still answers on hub: token status (no solver), some exfiltration and attacker-domain variants, a few
partner drafts and canary tasks. The loop's mean score (0.646) against the solvers' (0.986) is why most of the work went
into solvers.
