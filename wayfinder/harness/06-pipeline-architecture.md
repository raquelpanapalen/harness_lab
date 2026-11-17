# Pipeline architecture

Label: `wayfinder:grilling`
Status: closed
Assignee: Raquel (session)
Blocked by: 01-baseline-failure-analysis.md, 02-task-taxonomy.md, 04-model-capabilities.md, 05-prior-art.md
Map: [New harness for Harness Arena](map.md)

## Question

What are the stages of the hybrid harness (prepare workspace, classify task, model loop, guard, verify, submit), what does each stage hand the next, and where does each one use code vs. which model?

## Resolution

Decided with the human (grilling, two rounds, all recommendations accepted):

1. Stages per run: **run setup** (download, parse, index + SQLite, start image transcription) → per task **intake**
   (family, task kind, answer shape, allowed actions) → **solve** (family solver if one exists, else the generic loop)
   → **check** (code: answer shape, citations, agent ids backed by evidence, URLs defanged, actions allowed; at most one
   repair round) → **submit**, always.
2. Intake: a Jev `choice` question over a catalogue of family *descriptions* (what is asked, from which evidence), never
   dev wording; "other" or low confidence → generic loop. Task kind and allowed actions from the instruction via the
   same Jev call plus code rules.
3. A family solver returns a candidate answer, evidence handles and a confidence flag. On failure or low confidence,
   the generic loop takes over, seeded with the solver's partial facts as notes. (Which solvers: Family solvers.)
4. Generic loop = notebook loop: each call sees a fixed-size prompt (task, allowed actions, answer shape, notebook,
   latest tool result truncated); old tool outputs are never re-sent; prompt ≤ ~8k tokens; step cap by level (e.g.
   10/14/18; exact numbers in Model roles and budget per level).
5. A separate **compose** call writes the submission as structured JSON (outcome, answer, cited handles, plus any
   state-changing actions) against a schema built from the instruction's stated shape. The loop never submits.
6. Our own thin Python on `arena/` (client, models, cost tracking, Jev) with Pydantic models for schemas; no agent
   framework.
7. Run setup blocks task start (~1–2 min); image transcription continues in the background, and a task waits only for
   transcripts of its own images.
8. Loop tools: `search(query, path?)` (BM25 → handles + snippets), `lookup(kind, id)` (agent, token, trace, request
   id), `open(handle, around?)`, server read-only tools (`threat_intel`, `access_path`, `get_containment`),
   `note(fact, handles)`, `done()`. Identical repeat calls are answered from a cache with a "seen" note.
   State-changing tools are not loop tools: they are part of the compose output, so check sees them before they run.
9. Notebook: facts only via `note`, each with ≥ 1 handle (handle-less facts dropped); capped at ~2k tokens, oldest
   facts condensed by code. Compose may cite only handles in the notebook or the solver's evidence.
