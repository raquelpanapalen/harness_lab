# Spec: the new Harness Lab harness

The destination of the [map](map.md): every decision needed to build the harness that replaces the kit baseline in
`agent.py`. Each section names the ticket that decided it; the ticket holds the reasoning and evidence. Terms are in
`GLOSSARY.md`.

## 1. Objective and measurement

- **Metric: the leaderboard Score**, `Score = A · (0.5 + 0.5 · C)`. C is log-scaled on the event's fixed range: 1 at
  $0.049 per run, 0 at $1.135. Bars: AIT 0.750 (A 0.751, $0.049), Donaufriends 0.524. Kit baseline on development
  runs: A 0.262, $0.68, Score 0.152. `uv run harvester/report.py <results>` prints A, cost, C and Score per run.
- **Target ≤ $0.10 per run** (C ≥ 0.77). Hard limits: final-legal models only (Gemma 4 31B, Jev, Qwen embeddings via
  OpenRouter), 30 minutes, the container.
- **Data:** 39 development runs for building and tuning; 5 held-out runs (`--only-runs 5`) used only to report Score at
  milestones; their traces and tasks are never read. Compare whole runs, never single tasks (per-task noise 0.12–0.24).
  Measurement runs go one at a time. Dev spend cap $40 (≈ $11.6 spent before the build).
- The harness never reads `harvester/data/`.
- _Decided in:_ [Model roles and budget per level](09-model-roles-budget.md); charting notes in [map](map.md).

## 2. Shape: a per-task state machine

```
SETUP (once per run)
  └─ per task:  INTAKE → SOLVE ─┬─ solver confident ────┐
                                └─ else → LOOP ─────────┤
                                                        ▼
                                COMPOSE → CHECK ──ok──→ SUBMIT → DONE
                                            └ fail → REPAIR → CHECK (once) → SUBMIT (cleaned)
      any error, deadline or budget limit ────────────────────────────→ FALLBACK_SUBMIT
```

- An enum plus one function per state returning the next; no agent or graph framework. Our own thin Python on
  `arena/` (client, models, cost tracking, Jev), Pydantic models for schemas.
- The task record (state, notebook, handles, draft answer, attempt counts) is saved to
  `runs/<run>/state/<task>.json` after every transition. Retries resume from the last state; a restarted process picks
  up the same active run.
- _Decided in:_ [Pipeline architecture](06-pipeline-architecture.md) (incl. addenda).

## 3. Setup: workspace index

- Fetch the run manifest once; download every distinct blob in parallel (≈ 950, ≈ 100 MB). Parse text, csv, json,
  xlsx, ods, docx and odt at once; PDFs and images only when a task needs them. Setup ≤ 3 min; blocks task start.
- **Evidence units** are exactly what the grader cites: line (text/md/jsonl), record (csv/json), sheet + row
  (xlsx/ods), paragraph (docx/odt), page (pdf), path (images). Each carries path and locator, and an `untrusted` flag.
- Every csv, sheet and the audit log goes into SQLite, one table per blob hash. Local BM25 over evidence units. No
  embeddings in v1.
- Read-only tools (`tree`, `list`, `read`, `search`, `query_logs`) are served from the index. Server calls only for
  `threat_intel`, `access_path`, `get_containment`, state-changing tools and `submit`.
- A task sees only the blobs in its own manifest; derived facts are cached per blob hash, so per-task variants (audit
  log, board) never leak between tasks.
- **Images** are transcribed only when needed: tickets until the first OPEN one, the standing order once per run, scans
  for scan tasks; cached by hash. One generic prompt: verbatim text plus marks (stamps, signatures, handwriting,
  strike-throughs, dates). Lowest image budget first; raise it only for image kinds that measure badly on dev
  (scanned domains, standing-order settings, ticket status). Transcripts are untrusted text, cited by path. No Jev on
  images.
- **Untrusted flag** (structural signals only, never marker phrases): audit `detail` of ≥ 15 words, a live URL with a
  path or query, Office comment parts, invisible PDF text (`3 Tr`, minding real OCR layers).
- _Decided in:_ [Workspace index and retrieval](07-retrieval.md), [Image inputs](12-vision.md),
  [Injection landscape](03-injection-landscape.md).

## 4. Intake

- One Jev call per task: a `choice` over a catalogue of family **descriptions** we write (what is asked, from which
  evidence), never dev wording; plus task kind and allowed actions. "Other" or low confidence → the generic loop.
- Output: family, task kind, answer shape (from the instruction's stated format), and an **allowlist** of operations
  (`containment:quarantine_agent`, `containment:rotate_token`, `containment:block_domain`, `containment:add_note`,
  `send_message`, `write`), empty by default, following the grader's task-kind mapping.
- The workspace `/AGENTS.md` and the operator files it names are the only trusted text. Hard rules, outcomes and
  source tiers go verbatim into every loop and compose prompt (condensed by code only above ~1.5k tokens).
- _Decided in:_ [Pipeline architecture](06-pipeline-architecture.md), [Action guard](08-action-guard.md).

## 5. Solve: family solvers over a fact layer

- **Fact layer**, computed once per run and per audit-log/board version: attack traces, compromised set, patient zero,
  exfiltration transfers, attacker domains, permission graph with delegations, the 4 standing-order settings, roster,
  contacts.
- **Solvers implement runbook concepts**; every value that can vary is read from the workspace (standing order,
  POLICY.md, roster, contacts, change tickets). No dev ids, domains, timestamps or counts in code. Files and columns are
  found by header and content matching.
- **"Is this an instruction to agents?"**: decode first (base64, hex, zero-width, homoglyphs, split posts), then a Jev
  yes/no per artifact or post, cached by hash; uncertain (≈ 0.3–0.7) → Gemma. Measured on dev against expected traces
  before it is trusted.
- **Recognition is two-part:** intake picked the family AND the solver's preconditions pass (files and columns found,
  answer shape matches, implemented rule sections present). Every scenario-specific assumption is a checked
  precondition. Failure → loop.
- A solver returns answer, evidence handles and a confidence flag. Confident only when preconditions pass and nothing
  was ambiguous; otherwise its partial facts seed the loop as notes.
- **A solver is enabled only if it beats the loop on its family's development tasks** (by Score).
- **Build waves:** (1) fact layer + compromised agents, patient zero, timeline; (2) leaked tokens, access-log policy,
  access review, delegation chain, lethal trifecta; (3) inbox routing (refusals and actions); (4) exfiltration,
  canaries, breach notification, containment, rotation, first token read, token status, message origin; (5)
  model-heavy families (board posts, MCP rule 1, status claims, DPA amendments) get helpers only: decoders, a
  board-post prefilter, claim splitting, amendment date logic.
- _Decided in:_ [Family solvers](11-family-solvers.md), [Task taxonomy](02-task-taxonomy.md).

## 6. The generic loop (notebook loop)

- Each call sees a fixed-size prompt: the task, trusted rules, allowed actions, answer shape, the notebook, and the
  latest tool result (truncated). Old tool outputs are never re-sent. Prompt ≤ ~6k tokens.
- Tools: `search(query, path?)` (BM25 → handles + snippets), `lookup(kind, id)` (agent, token, trace, request id),
  `open(handle, around?)`, the server read-only tools, `note(fact, handles)`, `done()`. Identical repeat calls are
  answered from a cache with a "seen" note. No state-changing tools in the loop.
- Notebook: facts only via `note`, each with ≥ 1 handle (handle-less facts dropped), ≤ ~2k tokens, oldest condensed by
  code.
- Step caps 6 / 8 / 10 by level; each cap tuned by its effect on Score. Families where the loop scores near 0 on dev
  submit the best code-only answer instead of looping.
- Workspace text reaches the model inside data markers; `untrusted` units carry a visible "suspected planted text"
  tag; the system prompt says workspace text is evidence, never instructions.
- _Decided in:_ [Pipeline architecture](06-pipeline-architecture.md), [Model roles and budget](09-model-roles-budget.md).

## 7. Compose, check, submit

- **Compose** (one Gemma call): structured JSON against a schema built from the instruction's format: outcome, answer,
  cited handles, and any state-changing actions. It may cite only handles in the notebook or the solver's evidence.
- **Normalisation (code):** JSON strings parsed; numbers as numbers; top-level timestamps `YYYY-MM-DDTHH:MM:SSZ` in UTC
  (CET archive converted); duplicates removed; lists sorted when asked; object keys exactly as named.
- **Outcome:** OK unless a refusal has a code-verified reason: DENIED_SECURITY = sender not a verified contact or
  flagged in the change log; NEEDS_CLARIFICATION = target matches > 1 roster agent; UNSUPPORTED = no tool can do it.
  Any non-OK outcome empties the allowlist.
- **Citations:** the evidence units the answer rests on (one per item for lists), plus the rule file when the answer
  applies its rule; never the ticket or inbox message carrying the task unless the answer is about it. Cap = items + 2,
  or 4 for single-value and object answers.
- **Guards (deterministic code, no model):**
  - every action must be on the allowlist, and its arguments pass: quarantine targets in the roster and backed by
    trusted evidence, critical agents with their roster stand-in; rotation times from the code scheduler or checked
    against the change calendar and standing-order freezes; `send_message` only to a verified contact named by the
    task; `block_domain` only evidence-backed domains; `write` only to the named path;
  - frame guard: an agent id in a structured answer or a quarantine needs trusted backing (audit actor/action fields,
    registry, unflagged files), never free text such as audit `detail`; unbacked ids are dropped from lists, or trigger
    repair for single values;
  - outgoing text is rewritten, never refused: URLs not on a verified company domain defanged, token/secret values
    stripped, verbatim operator-rules text removed.
- **Repair:** one round, with the concrete check failures. If it still fails, submit the cleaned answer without the
  blocked part. A blocked action is never sent.
- **Submit as soon as check passes.** Fallback ladder: solver answer (even unconfident) → compose from the current
  notebook → a right-shaped empty answer (`[]`, `{}`, `null`) with outcome OK and no citations. Never a refusal as a
  fallback.
- _Decided in:_ [Submission and fallback policy](10-submission-policy.md), [Action guard](08-action-guard.md).

## 8. Models, budget, pacing

- **Gemma:** loop steps, compose, image transcription, uncertain instruction judgments, status-claim parsing.
  Reasoning off, temperature 0. **Jev:** intake, "instruction to agents?". **Embeddings:** none in v1.
- **Concurrency caps:** Gemma ≤ 6 in flight (a 429 halves it, +1 per error-free minute), Jev ≤ 10, embeddings ≤ 2,
  Task API 15 requests/s shared.
- **Retries:** 429/5xx/408/timeouts → exponential backoff with jitter, honour `Retry-After`, bounded by the task's
  deadline. 400 → retry once, then shrink context (halve the tool result, condense the notebook); log the error body and
  provider. 403 → stop model calls, FALLBACK_SUBMIT every open task.
- **Timeline:** setup ≤ 3 min; confident-solver tasks first, then loop tasks; soft deadline 24 min (no new loop steps,
  compose); hard deadline 27 min (FALLBACK_SUBMIT); done by 28.
- **Runaway watcher:** no new loop steps at $0.50; FALLBACK_SUBMIT at $1.00.
- _Decided in:_ [Model roles and budget](09-model-roles-budget.md), [Provider errors, retries and
  pacing](13-resilience.md), [Model capabilities](04-model-capabilities.md).

## 9. First experiments once v1 runs

1. Image budget per image kind (accuracy of scanned domains, standing-order settings, ticket status vs. cost).
2. Jev accuracy on "instruction to agents?" against the dev traces; whether the uncertain band needs Gemma.
3. Per family: solver vs. loop vs. code-only answer, by Score.
4. Loop step caps and prompt size, by Score.
5. Compose with reasoning low on L2/L3.
6. Jev "enough evidence to stop?" in the loop.

## Left to the build

- Prompt wording per stage: settled by measuring Score on development runs.
- Slicing into build tickets, in dependency order: skeleton and state machine → setup and index → compose, check and
  guards → fallback and pacing → solver wave 1 → loop → later waves. Each slice measured on development runs as it lands.
