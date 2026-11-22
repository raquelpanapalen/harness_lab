# 17: v2: find workspace files by role, not by path

**What to build:** The harness finds every input file by what it is (its content and the operator rules), never by a
hard-coded path or file name, and learns id formats from the workspace. Goal: hold the hub Score (0.965) and the port
reskin Score (0.962) while lifting the `layout` reskin from 0.258 to the same level, at ≤ $0.05 per run.

**Blocked by:** 16

**Status:** ready-for-agent

Spec: [SPEC.md](../SPEC.md) · Starting point: tag `v1.1` · Why: [docs/reports/v1.md](../../../docs/reports/v1.md) §3–5

## Context (read first)

- `AGENTS.md`, `CLAUDE.md` (rules: never read `harvester/data/` from the harness, journal every decision, commit before
  each measurement run, measurement runs one at a time).
- `docs/architecture.md` (how the harness works), `docs/development.md` (workflow, tests, reskins, adding a solver),
  `docs/reports/v1.md` (v1's numbers and weaknesses), `harvester/synth/README.md` (theme reskins).
- The organisers said the final keeps the task types under a different theme. Whether it also renames files is unknown;
  v2 must not care.

## What v1 hard-codes (as of `v1.1`)

Find them again with:
`grep -rnE '"/(fleet|board|logs|integrations|plans|artifacts|security|tickets|inbox|intel|notes|reports)\b|TOK-|INC-|(agents|tokens|token_fingerprints|registry_export|changes|contacts|swarm_board|egress_export|pending_access_requests|access_policy|canaries|change_calendar|enterprise_accounts)\.' harness`

| File | Lines (at v1.1) | What |
|---|---|---|
| `harness/intake.py` | 140–195 | open ticket only under `/tickets/`, inbox only under `/inbox/`, operator files by name |
| `harness/localtools.py` | 26–28, 212, 219 | search order list of hub paths; `query_logs` reads `/logs/audit/`, `/logs/archive/` |
| `harness/facts/runbook.py` | 345–367, 466, 524, 538, 794, 855, 943–944 | roster, change log, artifacts index, plans, evidence prefixes by path |
| `harness/index.py` | 51, 65 | path-based assumptions in the index |
| `harness/solvers/access.py` | 86, 223, 225, 429 | `/board/`, `/logs/access/` + "polic"/"request", `/fleet/` + "access_policy" |
| `harness/solvers/judged.py` | 32, 173, 424 | board under `/board/`, MCP under `/integrations/mcp/`; status-claim template sentences (~262–280) |
| `harness/solvers/response.py` | 20, 277, 283, 453, 468 | `TOK-` regex, draft/notes paths |
| `harness/solvers/incident.py`, `inbox.py` | 189, 171 | board path, `INC-` regex |
| `harness/guards.py`, `compose.py`, `loop.py`, `facts/runtime.py` | 442, 93, 112, 42 | path checks |

`/AGENTS.md` and `/drafts/` are fixed by the Task API docs and may stay literal.

## Approach

1. **One file-role resolver** (e.g. `harness/roles.py`), computed once per run from the index, used everywhere a path is
   hard-coded today. Each role is recognised by content, with the operator files as hints:
   - roster: the table whose rows hold agent ids plus a name, role and owner; token inventory: token ids + owner agent;
     fingerprints: token ids + 4-char / 12-hex columns; registry export: JSON with agents, scopes, delegations;
     IAM change log: markdown table of changes with approved / red-team / flagged sections
   - audit log: JSONL with `ts`/`agent`/`action`; rotated archive: CSV with local-time stamps and the same columns
   - board: JSONL of posts with id, author, text; tickets: files with a `status:` front matter line or an
     OPEN / CLOSED stamp in their transcript, wherever they are; inbox messages: mail headers
   - MCP manifests + approved-hash list + policy; access-log requests + policy; pending access requests + access policy;
     containment plan; egress register; canary list; change calendar; contacts (verified column)
   - `AGENTS.md` and the runbook name the operator files ("tonight's rules are on the signed standing order
     (/standing_order.jpg …)"): read those names from there
   A role that is ambiguous or missing raises the solver's `Precondition` (→ loop), never a guess.
2. **Ids from the data**: token ids from the inventory, incident id from `AGENTS.md`, agent id shape from the roster,
   instead of `TOK-` / `INC-` literals.
3. **Every model call bounded by a timeout** (a hung call stalled a whole measurement run for over an hour in v1).
4. Keep behaviour identical on hub: same answers, same citations.

## Measuring

- Unit tests for the resolver on hub-shaped and renamed workspaces (fixtures, no `harvester/data`).
- **Develop on dev runs**: reskin a few *dev* runs with the layout theme
  (`uv run --with pillow harvester/synth/reskin.py --theme layout --runs <dev run ids> --out harvester/data/synth/layout-dev`)
  and check solvers there (`tests/checks/check_solvers.py` on that data dir may need a `--data` option).
- **Measure once at the end** on the 5 held-out runs in all three forms, one run at a time:
  hub (`--only-runs 5`), `harvester/synth/measure.sh port`, `harvester/synth/measure.sh layout`.
  The 5 held-out runs have been read task by task (port work), so also build one new theme nobody has looked at
  (e.g. a hospital or bank, extending `layout`) and report it as the clean hold-out.
- Report in `docs/reports/v2.md`, same structure as `v1.md`; update `docs/results.md`; JOURNAL entries; tag `v2`.

## Done when

- [ ] No hard-coded workspace paths or id prefixes left in `harness/` except `/AGENTS.md` and `/drafts/`
- [ ] All unit tests, `tests/test_imports.py`, ruff, `test_replay.py`, `check_probes.py` pass
- [ ] Hub held-out Score ≥ 0.96, port ≥ 0.96, layout (all 5 runs) ≥ 0.93, new unseen theme reported; each ≤ $0.05 per run
- [ ] `docs/reports/v2.md`, `docs/results.md`, JOURNAL updated; tag `v2`
