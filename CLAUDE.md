@AGENTS.md

# Project notes (this fork)

- Event is over; the team key is for post-event dev runs only. No final submission is planned.
- Develop **offline** against the replay server, not the live arena: `uv run harvester/replay/server.py` (port 3998),
  then point `ARENA_BASE_URL=http://localhost:3998`. Hold out runs with `--exclude-runs N` / `--only-runs N` and
  report scores on the held-out runs only.
- `harvester/data/` (2.4 GB, git-ignored) holds the expected answers. The harness must never read it: it is for the
  replay server and evaluation only (AGENTS.md: do not memorise expected answers).
- `docs/task-api.md` is the full Task API reference (tool formats, scoring, outcomes, citations).
- The harness is `harness/` (`main.py` runs it by default; `docs/architecture.md` explains it), specified in
  `wayfinder/harness/SPEC.md` (decisions and evidence in the map's tickets, `wayfinder/harness/`; terms in `docs/glossary.md`).
  The starter-kit agent is kept for comparison in `baseline/` (`main.py --harness baseline`). Status reports per
  version: `docs/reports/`. Theme robustness: `harvester/synth/` (reskinned held-out runs).
- Tests: `tests/run_all.sh` (unit tests, offline); dev-run checks that read `harvester/data` are in `tests/checks/`,
  per-family evaluators in `harvester/evals/`.
- Judge changes by the leaderboard Score, `A · (0.5 + 0.5 · C)` with C log-scaled from 1 at $0.049 to 0 at $1.135 per
  run (`uv run harvester/report.py` prints it). Target ≤ $0.10 per run. Run measurement runs one at a time.
- After changing `harvester/replay/`, run `uv run harvester/replay/test_replay.py` and
  `uv run harvester/replay/check_probes.py`; both must pass.
- Use the live arena only for new harvests or grader calibration (`harvester/harvest.py`, `harvester/calibrate.py`).
