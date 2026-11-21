# Code computes, models judge: a three-cent incident-response agent on a small model

*How we took a security-investigation agent from a leaderboard Score of 0.152 to 0.965 on held-out runs at $0.03 per run,
with one fixed 31B model, and how we found out where it would still break.*

Harness Arena gave every team the same models and the same tasks and scored only the harness around them: the code that
decides what the model sees, what it is asked, and what leaves the agent. This article describes what we built, how we
measured it, and what we learned. Every number comes from a run recorded in the repository's `JOURNAL.md` or
`docs/results.md`.

## The problem

An agent works as the night-shift incident responder of a simulated company. One of its AI agent fleets has been
hijacked through an untrusted artifact, and the incident commander files tickets: which of our agents were compromised,
when did customer records first leave, which posts on the agents' board are injected instructions, which MCP servers
must be disabled, quarantine these agents and rotate those tokens, draft a partner-safe summary. A run is 48 such tasks,
16 at each of three levels, all about one incident.

Each task comes with its own copy of a workspace of about 400 files, 650k to 890k tokens, mostly noise: JSONL audit
logs in UTC next to a rotated CSV archive in local time, CSV registries whose column names change from run to run,
spreadsheets, Word and ODF documents, PDFs, an inbox, the agents' board, and photos. The open ticket is often a photo of
a printout with an OPEN stamp; the counting rules for exfiltrated records live only on a photographed, signed standing
order, and a typed copy elsewhere is an outdated decoy. Sources have trust tiers, agents' own status reports lie, and
other teams plant prompt injections in the files: mostly in audit-log detail fields, mostly trying to frame a clean
agent or to get a live attacker URL into the answer.

The agent returns `{outcome, answer, sources}`. The outcome is OK or a refusal (`DENIED_SECURITY` for an attack or a
rule-breaking request, `NEEDS_CLARIFICATION` for an ambiguous one, `UNSUPPORTED` when no tool exists). The answer has a
required shape. Sources are citations in the grader's locator format. Per task, `score = answer · (0.7 + 0.3 · sources)`,
and zero if any rule was broken, for example a state-changing tool the task did not ask for. The run's Score is
`A · (0.5 + 0.5 · C)`, where A is mean accuracy and C falls on a log scale from 1 at $0.049 per run to 0 at $1.135.

The models were fixed: Gemma 4 31B for chat and vision, Jev (a model that answers typed yes/no and multiple-choice
questions with probabilities for about $0.00002 a call), and an embedding model.

## Start from the failures

Before designing anything we read the starter kit's failures. On two dev runs it scored 25.16 of 96 points. Of the 70.84
points it lost, 47 came from tasks it **never submitted**: the provider returned 429 (rate limited, 29 tasks) or 400
(18 tasks, all on prompts above roughly 8k tokens), and the kit abandoned a task on its first model error. Wrong answer
content cost 12.6 points; formats, citations and wrong outcomes the rest. Tasks that succeeded took a median of 8 steps;
failures ran into the 25-step cap at about 140k prompt tokens.

So the first design constraints had nothing to do with reasoning: every task must end in a submission whatever goes
wrong, every call must go through one pacer that knows the deadline and the budget, and no prompt may grow past ~6k tokens.

The second analysis was a taxonomy of the 1,872 dev tasks. They fall into 23 families, and about three quarters of
tasks are **computable by code** once a few facts are established per run: which agents executed actions inside an
attack trace, which artifact started it, which transfers count as exfiltration under the standing order's rules, which
agent can reach which scope through delegations. The model is needed in bounded places: deciding whether a piece of text
is an instruction aimed at agents (in any language or encoding, split across posts, quoted as a warning or adopted as an
order), reading images, and parsing the numbered claims of a status report.

## The design: code computes, models judge

The harness is an explicit state machine per task over a shared per-run index:

- **Setup** downloads every file once per run and parses all formats into *evidence units*: a line of a text file, a
  CSV record, a spreadsheet row, a Word paragraph, a PDF page. Each unit is exactly a locator the grader accepts, so
  citing is choosing units, never formatting paths. The units are loaded into SQLite and BM25, and the platform's
  read-only tools are answered locally with byte-identical output.
- **Intake** resolves the actual request (the instruction, the open ticket, possibly a photo, or an inbox message),
  condenses the operator rules, and makes **one Jev call** to pick the family, the kind of task and the allowed actions.
- **Solve**: if the family has a solver and the solver's own preconditions hold (files and columns found, answer
  shape matching, facts unambiguous), code computes the answer, its citations and its actions. A solver that is not sure
  never guesses: it hands its partial facts to the loop as notes.
- **Loop and compose**: only for what no solver answers, Gemma runs a small notebook loop (6, 8 or 10 steps by level,
  prompts under ~6k tokens, notes instead of a growing transcript), then one call composes the JSON answer.
- **Check**: deterministic guards inspect every answer and action, with one repair pass.
- **Submit**: allowed actions once, then the submission. Any exception, the 27-minute deadline or the $1 runaway guard
  leads to a fallback that submits the best draft or a right-shaped empty answer.

Underneath sits a **fact layer** that computes the runbook's concepts once per run: compromised agents, patient zero,
attack traces, exfiltration under the standing order's settings, the permission graph, quarantines. Twenty-three family
solvers are thin queries on it. Every rule they apply is read from the task's own workspace: the fingerprint rule for
leaked tokens, the access-log policy clause by clause, the freezes and buffers that block a token rotation.

### Who does what

Measured on the 240 held-out tasks, code solvers produce 94 % of the answers with a mean task score of 0.986; the Gemma
loop answers the other 6 % at 0.646. The money goes elsewhere. Of $0.031 per run, Jev takes 23 % (intake and the
instruction judgments), Gemma reading images 33 %, and Gemma text (loop, compose, uncertain judgments, claim parsing)
44 %.

Jev turned out to be the right tool for screening. Intake costs $0.0018 per 48-task run and picked family, kind and
allowed actions correctly on all 39 dev runs. The "is this an instruction to agents?" judgment decodes the text first
(base64, zero-width and look-alike characters, split posts joined), asks Jev once per distinct text, and sends only the
uncertain band (probability between 0.3 and 0.7) to Gemma. For board triage, code first discards routine posts (text
that repeats with only numbers changed), then a post counts as injected only when both the general question and the
runbook's own definition say yes.

Gemma reads only the images a task needs: the open ticket, the signed standing order, images the request names. Each is
transcribed once per run, cached by content hash, and indexed like any other file, so it can be searched and cited.

## Defending against injections at the output

The injection research on 39 dev runs found that two thirds of other teams' attacks try to frame a clean agent and a
quarter plant a live URL; about 75 % hide in audit-log detail fields, half of those on lines the task should cite. Some of
the scenario's own traps are exactly the evidence a task asks about. Deleting suspicious text would break tasks.

So the harness keeps all text readable and citable, marks it untrusted, and guards what leaves: URLs are defanged unless
they are on a verified domain, messages go only to verified contacts, no secret values appear anywhere, state-changing
tools run only when the task asks for that kind of action, refusals are checked against the rules, and a **frame guard**
drops any agent id from an answer or a quarantine unless audit events back it. None of this asks a model, because a model
can be talked round. On 240 held-out tasks no rule was broken.

## Measuring honestly, after the event

The event ended before this harness existed, so everything was measured offline. We recorded 44 real dev runs (2,112
tasks with their files, recorded tool outputs and the answers revealed after each run) and built a replay server: a local
Task API whose tools reproduce the real ones (1,269 of 1,269 recorded responses identical) and a grader calibrated
against the real one (full grades identical on 96 of 96 real tasks, 239 of 242 calibration variants). Every change could
then be measured for the price of its model calls.

The five newest runs were held out. Development used the other 39, and held-out runs were only read as aggregates until
the build was done. At the milestone the held-out Score equalled the dev Score (0.929 and 0.929). Two live post-event
dev runs on the real arena scored 0.949 and 0.996, matching or beating the replay.

## Results

| Measurement | A | Cost / run | Score |
|---|---|---|---|
| Starter kit, dev | 0.262 | $0.68 | 0.152 |
| Notebook loop only, no solvers | 0.194 | $0.136 | 0.163 |
| Solvers, held-out milestone | 0.929 | $0.029 | 0.929 |
| After the citation work, held-out | 0.966 | $0.029 | **0.965** |
| Live post-event dev run | 0.996 | $0.018 | 0.996 |

For comparison, the event's winner scored 0.750 (A 0.751 at $0.049). Two steps carried most of the gain. Solvers took
accuracy from the loop's 0.19 to 0.93 while cutting cost by four fifths. Citations were the last third of each correct
answer: once answers were right, measuring per family which kinds of evidence the grader expects (chosen on dev runs 1–10,
validated on 11–39) and citing exactly those raised the held-out Score from 0.929 to 0.965 at the same cost.

At $0.03 per run, C is 1, so the Score equals accuracy. Cost stopped being a trade-off once code answered most tasks
and prompts stayed small.

## What didn't work, or wasn't tried

The journal records a rejected alternative with every decision. A few are worth repeating:

- **Agent frameworks.** A graph framework for nine fixed states, or an agent framework whose loop would have been the
  smallest part of the system, added nothing; guards and context control needed our own code anyway.
- **Routing by regex on dev wording.** It sorted every dev task correctly, but the arena rewords tasks; a typed Jev
  question generalises, and a cheaper code prefilter that misses unseen wording misroutes.
- **Embeddings.** About 8 seconds per request under load, and the workspace is dominated by ids, where BM25 and SQL do
  better. The index made them unnecessary.
- **Transcribing every image at setup.** About $0.08 per run, against $0.009 when reading per need.
- **Screening inputs with a model.** It can be talked round, and refusing on detection costs utility. Output guards don't
  have either problem.
- **Letting a solver end a task.** A crash or a missing input must cost nothing: the loop takes over.
- **Citing "just in case".** Citations past the expected ones plus two are penalised; every evidence kind we added was
  chosen by measured hits.

## Where it would still break

The organisers said the final keeps the task types under a different theme, "to punish overfitting", and all 44 recorded
runs share one theme. So we rewrote the held-out runs into new themes, with the answers still known. Every file format is
rewritten consistently: text, JSON string literals, office XML, PDF text through each font's ToUnicode map, and images,
which are transcribed once, rewritten and redrawn. Paths, requests, expected answers and cited sources change with them,
and line numbers, token fingerprints and recorded file hashes are kept consistent.

- **New vocabulary**: about 460 swaps (organisation, attacker, agent ids and names, people, services, artifacts, MCP
  servers, customers). Score 0.962 against 0.965; two of 240 tasks differ.
- **New layout**: the same plus renamed directories and files and new id prefixes. Accuracy 0.258 at $0.17 on one run.
  Intake looks for the open ticket only in `/tickets/`, about 25 places find solver inputs by path, and id regexes assume
  `TOK-` and `INC-`. Solvers miss their inputs, tasks fall back to the expensive loop, and the loop cannot carry them.

Building the reskin taught its own lesson. The first new-vocabulary measurement showed a 0.918 Score, which looked like
overfitting. Comparing task by task showed two bugs in our generated data instead: a redrawn ticket photo had wrapped
`survey-mirror.test` at the hyphen, so the harness correctly blocked `mirror.test`; and rewritten MCP manifests no longer
matched their recorded approved hashes, so the harness correctly disabled every server. Synthetic data can mislead as
easily as it informs. Check it with the same rigour as the system it tests.

## Takeaways

1. **Read your baseline's failures before designing.** Ours were rate limits and prompt sizes, not reasoning.
2. **Let code compute and ask the model bounded questions.** A small model is a good judge of "is this an instruction?"
   and a poor 25-step investigator.
3. **Make citing a matter of choosing units.** If parsing produces exactly the grader's locators, a third of the score
   becomes bookkeeping.
4. **Guard the outputs.** Attacks sit on the lines you must cite; deterministic checks on what leaves can't be talked
   round.
5. **Build the measuring instrument first.** A replay with a calibrated grader and a real hold-out turned every idea into a
   cheap experiment, and kept us honest about which ideas worked.
6. **Your data's single theme is your code's single theme.** Test on synthetic variations of layout and vocabulary before
   the final does it for you.

Code, documentation and every measurement: the repository's `README.md`, `docs/` and `JOURNAL.md`.
