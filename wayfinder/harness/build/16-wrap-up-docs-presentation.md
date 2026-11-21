# 16: Wrap-up: clean code, tidy repo, documentation, presentation and article

**What to build:** The final state of the repo, ready to show: clean harness code, a clear repo structure, complete
documentation that explains the solution, its findings and its results, plus an HTML presentation and a technical
article on the work. No change in harness behaviour: the held-out Score must stay what it is (0.965 on hub, 0.962 on
the port reskin, at ≈ $0.03 per run; reports per version in `docs/reports/`).

**Blocked by:** 15

**Status:** in progress: sections 1–2 done (2026-11-21, see JOURNAL 19:30); `ruff format` deliberately not applied; sections 3–5 open

Spec: [SPEC.md](../SPEC.md)

## 1. Clean the final code

- [x] Remove dead code and leftovers: unused functions, stale TODOs, debug prints, commented-out blocks; check
  `harness/wave4.py` vs `harness/solvers/wave4.py` (duplicate or split by accident?) and merge or rename clearly
- [x] Rename files, modules, functions and variables where a clearer name helps a reader understand them
  (e.g. build-history names like `wave2.py` / `wave4.py` / `wave5.py` → names for what they solve, such as
  `solvers/inbox.py`, `solvers/rotation.py`; vague names like `core.py`, `world.py`, `judge.py` checked too). List the
  renames (old → new, one-line reason) in the layout proposal for the human to approve, update every import and doc
  reference, and use `git mv` so history follows
- [x] Decide the fate of the kit baseline (`agent.py`, `--harness baseline`): keep as a documented comparison point
  or move to `baseline/` — never break `main.py`'s default (lab) path
- [x] Consistent module docstrings (one paragraph each: what it does, which SPEC section it implements)
- [x] Lint/format pass (e.g. `ruff check` + `ruff format`) with the config in `pyproject.toml`; no behaviour changes
- [x] No secrets, keys, live attacker URLs or expected answers anywhere in tracked files (grep before committing)

## 2. Improve the repo structure

- [x] Propose the target layout to the human first (one short tree in the PR/commit message), then move:
  e.g. `harness/` (agent), `arena/` (client), `harvester/` (replay, grader, synth, evals), `tests/` split into
  unit tests (`test_*.py`) and dev-run checks (`check_*.py`, `eval_*.py`), `docs/` for long-form docs
  (`docs.md` → `docs/task-api.md`, `HARNESS.md`, `FINAL_RUN.md`, `GLOSSARY.md`), `examples/` kept or dropped
- [x] Root stays small: `README.md`, `AGENTS.md`, `CLAUDE.md`, `JOURNAL.md`, `Dockerfile`, `main.py`, `pyproject.toml`
- [x] Every moved path updated in imports, docs, `Dockerfile`, `.dockerignore`, CLAUDE.md, wayfinder links
- [x] `.gitignore` covers `__pycache__/`, `runs/`, `harvester/data/`, generated caches; remove any tracked junk
- [x] Organiser material in `slides/` (AIM pdf/pptx) kept apart from our own presentation (e.g. `docs/organiser/`)

## 3. Documentation

- [ ] `README.md` rewritten as the front door: problem in 3 sentences, a short task table (family, input,
  output) linking to `docs/tasks.md`, the solution in one diagram, results table
  (kit baseline → lab harness on dev, held-out hub, held-out port reskin, live dev run), quickstart (replay server +
  one run), repo map, links to the deeper docs
- [ ] `docs/tasks.md`: the problem itself, before any solution. What a run is (48 tasks, levels 1–3, one shared
  workspace per run), what the agent receives (the task instruction, the workspace files and their formats: text,
  csv, jsonl audit logs, xlsx/ods, docx/odt, pdf, images/photo tickets, the workspace `/AGENTS.md` and operator rules),
  which tools it may call (read-only vs state-changing), and what it must return (answer shape per instruction,
  outcomes such as OK / DENIED_SECURITY / NEEDS_CLARIFICATION / UNSUPPORTED, citations with grader locators). Then one
  entry per task family (all 23 from [02-task-taxonomy](../02-task-taxonomy.md)): what it asks, its typical input, the
  expected output shape with a made-up example (never a real expected answer), the traps (injections, framing,
  refusals) and how the grader scores it. Sources: `docs.md`, tickets 02 and 03, family descriptions in
  `harness/intake.py`
- [ ] `docs/architecture.md` (from `HARNESS.md` + SPEC): state machine, intake, workspace index, fact layer, family
  solvers, notebook loop, compose, guards, pacing/resilience, vision; one diagram per major flow
- [ ] `docs/model-roles.md` (or a section of architecture.md): who does what — **code** vs **Jev** vs **Gemma**
  (vs embeddings, if used), with concrete examples from the harness. For each: which pipeline steps and task families
  use it, why it was chosen there (cost, reliability, the evidence from tickets 04 and 09), and one worked example
  traced through a real code path (file:function), e.g. code: workspace index, fact layer, a family solver computing
  an answer, guards, normalisation; Jev: intake family/kind/allowlist pick, the typed "instruction to agents?" yes/no
  judgment; Gemma: the notebook loop for families without a solver, image transcription, compose of free-text drafts.
  A table of family → who answers (code only / Jev + code / Gemma loop) and the share of tasks and of spend each takes
  on a typical run (measured from run traces, not guessed)
- [ ] `docs/findings.md`: what we learned, each with its evidence (run ids, numbers from JOURNAL and the closed
  tickets): provider errors cost two thirds of baseline points; ≈ 75 % of tasks computable by code; Jev for cheap
  typed screening; output guards + frame guard beat input filtering; citation work (+0.036 held-out); theme
  robustness (hub 0.965 vs port 0.962 on vocabulary; layout 0.258 on renamed files, docs/reports/v1.md); cost is re-sent context
- [ ] `docs/results.md`: every milestone measurement in one table with commit, runs, A, cost, C, Score; how Score
  is computed; how held-out discipline was kept
- [ ] `docs/development.md`: offline replay workflow, held-out rules, tests and checks to run, `harvester/synth`
  reskin, how to add a family solver
- [ ] `wayfinder/harness/map.md` and `build/README.md` updated (ticket 16 row, final state)
- [ ] All doc links checked (no dead relative links)

## 4. Presentation (HTML)

- [ ] Built with the `/frontend-slides:frontend-slides` skill, saved in the repo (e.g. `docs/presentation/index.html`,
  self-contained, opens offline)
- [ ] ~12–18 slides: the challenge (Harness Arena, fixed small models, Score formula) → the tasks (kinds of
  tasks, input, expected output, one worked example from `docs/tasks.md`) → baseline failure analysis →
  design principles (code first, model for judgment, always submit, safety in code) → architecture diagram → who does
  what (code vs Jev vs Gemma, with examples and the task/spend split) → family
  solvers + fact layer → injection defence → cost control → offline replay lab + calibrated grader → results
  (baseline 0.152 → held-out 0.965, $0.68 → $0.03/run; leaderboard bars AIT 0.750, Donaufriends 0.524) → theme
  reskin / overfitting → lessons learned → what's next
- [ ] Numbers on slides match `docs/results.md` exactly; charts drawn from those numbers

## 5. Technical article

- [ ] `docs/article.md` (≈ 2,000–3,500 words), written for engineers building cheap small-model agents: problem,
  the tasks (kinds, inputs, expected outputs, summarised from `docs/tasks.md`),
  approach, architecture, model roles (code vs Jev vs Gemma, with examples), the measurement setup (replay + grader calibration + held-out + reskin), key findings with
  numbers, what didn't work (rejected alternatives from JOURNAL), limitations (theme overfitting, Gemma judgment),
  takeaways
- [ ] Figures shared with the presentation where possible; every claim backed by a run id or ticket
- [ ] No team keys, no live attacker URLs (defang), no expected answers or held-out task contents

## Done when

- [x] All tests pass (`tests/test_*.py`), plus `harvester/replay/test_replay.py` and `check_probes.py` if
  `harvester/replay/` moved
- [x] One held-out replay run after the restructure reproduces the Score within noise (≈ 0.96, ≈ $0.03), so
  the cleanup changed nothing
- [x] `docker build` + one-task container test passes
- [ ] Commits in logical steps (cleanup, moves, docs, presentation, article), JOURNAL entry for the structure decision
