# Family solvers: which, and how they fail safe

Label: `wayfinder:grilling`
Status: closed
Assignee: Raquel (session)
Blocked by: 01-baseline-failure-analysis.md, 06-pipeline-architecture.md
Map: [New harness for Harness Arena](map.md)

## Question

The taxonomy says ≈ 75 % of tasks are computable by code from a few per-run facts. Which families get a code solver,
in what order (by points recoverable), how does a solver recognise its task without depending on exact dev wording, and
what happens when its inputs don't parse (fall back to the generic loop)? Which per-run facts are computed once and
cached, and how are per-task file variants (the audit log's planted attacks) kept from leaking between tasks?

## Resolution

Decided with the human (grilling, all recommendations accepted):

1. Solvers implement runbook concepts (attack trace, compromised, patient zero, current permissions…). Everything that
   can vary is read from the current workspace (standing-order and POLICY.md settings, roster, contacts, change
   tickets); no dev ids, domains, timestamps or counts in code; files and columns found by header/content matching.
   Missing inputs → fall back to the loop.
2. A shared fact layer computed once per run and per audit-log/board version (keyed by blob hash): attack traces,
   compromised set, patient zero, exfil transfers, attacker domains, permission graph with delegations, standing-order
   settings, roster and contacts. Solvers are thin queries on it.
3. "Is this an instruction to agents?": code decodes first (base64, hex, zero-width, homoglyphs, split posts), then a
   Jev yes/no per artifact/post cached by content hash; uncertain (~0.3–0.7) → Gemma. Accuracy measured on dev runs
   against expected traces before it is trusted.
4. Build waves: (1) fact layer + compromised, patient zero, timeline; (2) leaked tokens, access-log policy, access
   review, delegation chain, lethal trifecta; (3) inbox routing (refusal outcomes and actions); (4) exfil, canaries,
   breach notification, containment, rotation, first token read, token status, message origin; (5) model-heavy
   families get helpers only.
5. Recognition is two-part: intake's Jev family classification AND the solver's own preconditions (files/columns
   found, answer shape matches, implemented rule sections present). Any failure → loop.
6. A solver is enabled only if it beats the generic loop on its family's development-run tasks. At runtime it is
   "confident" only when preconditions pass and nothing was ambiguous; otherwise its partial facts seed the loop. No
   model double-check of solver output in v1.
7. Model-heavy families (board posts, MCP rule 1, status claims, DPA amendments) go to the loop with code helpers:
   decoders, a board-post prefilter (routine posts repeat verbatim; ~10–30 unusual per board), claim splitting,
   amendment date logic.
8. All recorded runs are one scenario ("hub"): every scenario-specific assumption is a runtime-checked precondition
   that falls back to the loop, never a silent default. The held-out score measures this one scenario only.
