# Synthetic themes (`synth/`)

The final uses the same kinds of tasks under a **different theme** (organisers: "to punish overfitting"). Every
harvested run is theme `hub` (MuggingFace, an open model hub). `reskin.py` rewrites harvested runs into another
theme so the harness can be measured on vocabulary it has never seen, with the answers still known.

```bash
uv run --with pillow harvester/synth/reskin.py --theme port --last 5        # the 5 held-out runs → data/synth/port
uv run harvester/replay/server.py --data harvester/data/synth/port --port 3997 --results harvester/replay_results/port
ARENA_BASE_URL=http://localhost:3997 ARENA_API_KEY=arena_local uv run main.py --kind dev --note "port theme"
```

Pillow is a dev-only extra (`--with pillow`), not a harness dependency.

## What changes

A theme (`themes/<name>.json`) maps every hub-specific string to its counterpart: organisation, attacker and their
domains (look-alikes included), incident and secret prefixes, agent-id prefixes, agent display names, roles and
squads, services and token-id codes, log targets, artifact kinds and names, MCP server names, customer organisations,
people and their e-mail addresses. `port` is Seamark Logistics, a container terminal operator, attacked by Blackreef
Shipping's Kraken-9 swarm.

The rewrite is one left-to-right pass (`textmap.py`), applied everywhere the same way:

| What | How |
|---|---|
| md, txt, csv, html | plain text |
| json, jsonl | string literals only, layout kept |
| docx, odt, xlsx, ods | XML text nodes |
| pdf | text-showing operators re-encoded through the font's ToUnicode map (`pdfedit.py`) |
| jpg, png | transcribed once by the fixed model (cached in `data/synth/transcripts.json`), rewritten, redrawn (`images.py`); open tickets use the exact known request |
| base64 and Unicode-tag runs inside text | decoded, rewritten, re-encoded |
| paths, instructions, requests, expected answers and sources, threat-intel feed, recorded tool outputs | same map |

Line counts never change, so cited lines stay valid. Token fingerprints (`sha256_12` of a secret value) are
recomputed for every secret value found in the run.

## Checks

`reports/<run>.json` in the output lists, per run: files changed by type, **leftovers** (hub strings still present
after the rewrite; expected only in the real-world filler PDFs that use words like "billing"), images redrawn, and
`request_match` for every open-ticket photo (transcript vs. known request; below ~0.95 means the redrawn image may
differ from the original).

## Limits

- Redrawn images are cleaner than the originals and lose their layout (chat screenshots become sheets), so image tasks
  may be slightly easier. A transcription error becomes part of the new image.
- Directory layout, file kinds and the task families stay as in `hub`, which is what the organisers said the final
  keeps. Column names already vary per run in the harvests.
- Don't tune on a theme you report on: build a second theme for the hold-out.
