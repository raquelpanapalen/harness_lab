# Development

How to change the harness and measure the change, offline. Rules for coding agents: [`AGENTS.md`](../AGENTS.md) and
[`CLAUDE.md`](../CLAUDE.md); every design decision goes into [`JOURNAL.md`](../JOURNAL.md).

## The offline lab

`harvester/` recorded 44 real dev runs (2,112 tasks: files, recorded tool outputs, expected answers) into
`harvester/data/` (git-ignored, ≈ 2.4 GB). `harvester/replay/` serves them as a local Task API with the real tools'
outputs and a grader calibrated against the real one (`harvester/README.md`). No arena key is needed; only model calls
cost money.

```bash
uv run harvester/replay/server.py --exclude-runs 5             # :3998, the 5 newest runs held out
ARENA_BASE_URL=http://localhost:3998 ARENA_API_KEY=arena_local \
  uv run main.py --kind dev --note "hypothesis"                # one run (48 tasks); add --level 1 --limit 6 to start small
uv run harvester/report.py --worst 15 harvester/replay_results/*.json   # A, cost, C, Score; lowest tasks
```

- **The harness never reads `harvester/data/`**: expected answers are for the replay server and evaluation only.
- Commit before each measurement run and pass `--note`; run measurements one at a time (cost is per run).
- After changing `harvester/replay/`: `uv run harvester/replay/test_replay.py` and `uv run harvester/replay/check_probes.py`.

## Held-out rules

- Develop on the 39 older runs (`--exclude-runs 5`); measure on the 5 newest (`--only-runs 5`) and report only those.
- Don't read held-out per-task results while a change is still being tuned: once you fix things by looking at them,
  they are dev data. The current 5 held-out runs have been read task by task for the theme work
  ([`results.md`](results.md)); build a new reskin theme for the next hold-out.

## Theme robustness

The final uses another theme. `harvester/synth/` rewrites runs into new themes with the answers still known
([`harvester/synth/README.md`](../harvester/synth/README.md)):

```bash
uv run --with pillow harvester/synth/reskin.py --theme port --last 5   # images: one transcription each, cached
uv run harvester/synth/check.py port                                    # line counts, sources, fingerprints, hashes
harvester/synth/measure.sh port "note"                                  # one run at a time, progress file
python3 harvester/synth/compare.py port                                 # per-task differences vs hub
```

Themes: `port` (new vocabulary), `layout` (port plus renamed directories, files and id prefixes). A change should hold
on hub, port and layout.

## Tests and checks

| What | Command | Model calls |
|---|---|---|
| Unit tests (state machine, intake, index, facts, solvers, guards, pacing, vision, imports) | `tests/run_all.sh` | none |
| Replay server self-test, probes against recorded server responses | `harvester/replay/test_replay.py`, `check_probes.py` | none |
| Lint | `uvx ruff check .` (config in `pyproject.toml`) | none |
| Workspace index vs recorded server responses, citation coverage | `uv run tests/checks/check_index.py all` | none |
| Fact layer on dev runs | `uv run tests/checks/check_facts.py` | Jev (cached) |
| Every solver on dev runs, graded, through CHECK | `uv run tests/checks/check_solvers.py [--family F]` | Jev (cached) |
| Per-family evaluators (intake, incident response, rotation, vision) | `uv run harvester/evals/eval_*.py` | Jev / Gemma (cached) |
| Container | `docker build -t harness . && docker run --rm --network host --env-file .env -e ARENA_BASE_URL=http://localhost:3998 -e ARENA_API_KEY=arena_local harness --tasks t01` | one task |

The checks in `tests/checks/` and `harvester/evals/` read `harvester/data/` (expected answers) and skip the held-out
runs; they are evaluation tools, never part of the harness.

## Adding a family solver

1. Find the family's requests on dev runs (`harvester/data/tasks.csv`) and what the runbook says about it. Add it to
   intake's family list (`harness/intake.py`, `FAMILIES`) if it is new.
2. Put any reusable concept in the fact layer (`harness/facts/runbook.py`): a `Fact(value, evidence, ok, problems)`,
   computed once per distinct version of its input files, with preconditions instead of guesses.
3. Write the solver in the matching module of `harness/solvers/` and register it:

   ```python
   @register("my_family")                       # enabled=False until it beats the loop on dev by Score
   def my_family(ctx, rec, facts) -> Solution:
       if rec.answer_shape not in ("list", "unknown"):
           raise Precondition("expected a list")   # → the loop handles the task
       f = facts(("artifacts",))                   # ask only for the facts it needs
       fact = f.compromised()
       return Solution(answer=sorted(fact.value), sources=[u.source() for u in fact.evidence],
                       confident=fact.ok, notes=[...], why="; ".join(fact.problems))
   ```

   A solver that is not sure returns `confident=False` with notes (the loop starts from them); it never ends a task.
4. Measure it: `uv run tests/checks/check_solvers.py --family my_family` on dev runs (confident answers must be right;
   a family is enabled only with 0 confidently wrong answers), then a replay run. Record the decision in `JOURNAL.md`.

## Repository map

| Path | What |
|---|---|
| `main.py` | entry point: starts or resumes a run, runs the harness (`--harness lab`, default) or the kit baseline |
| `harness/` | the harness ([`architecture.md`](architecture.md)): `machine.py` state machine, `intake.py`, `index.py` + `localtools.py` + `tool_router.py`, `facts/`, `solvers/`, `loop.py`, `compose.py`, `guards.py`, `normalise.py`, `pacing.py`, `vision.py` |
| `arena/` | Task API client (manifests, blob cache), model calls (`llm.py`), shared file parsers |
| `baseline/` | the starter kit's agent (`agent.py`) and a pi coding-agent baseline, kept for comparison |
| `harvester/` | harvesting, replay server and grader, calibration, `evals/`, `synth/` (theme reskins), `report.py` |
| `tests/` | unit tests (`test_*.py`, `run_all.sh`), mock server, `checks/` (dev-run checks) |
| `docs/` | task API reference, tasks, architecture, model roles, findings, results, final-run limits, glossary, `reports/`, organiser slides |
| `wayfinder/harness/` | the design map: research tickets, decisions (`SPEC.md`), build tickets |
| `research/` | the five pre-build research reports (baseline failures, task taxonomy, injection landscape, model capabilities, prior art), merged from their `research/*` branches |
