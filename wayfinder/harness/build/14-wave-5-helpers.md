# 14: Wave 5 helpers and code-only cut-offs

**What to build:** Model-heavy families (board posts, MCP rule 1, status-report claims, DPA amendments) get code helpers in the loop: decoders, the board-post prefilter, claim splitting, amendment date logic. For each family, the dev data decides whether the loop or a code-only answer scores higher by Score.

**Blocked by:** 06, 09

**Status:** done

- [x] Per-family Score with and without helpers, and loop vs. code-only
- [x] Decision per family recorded in the JOURNAL

Spec: [SPEC.md](../SPEC.md)

## Result

Code answers by themselves in all four families (`harness/solvers/wave5.py`). The model does only the bounded
judgments: Jev yes/no per post or description, and vision for report and amendment images.

- **board_injected:**
  - Prefilter: a post whose digit-normalised text repeats more than 3 times on the board is routine and isn't judged (about 34 of about 950 posts are left).
  - Split posts ("(1/2)", "[1/2]", "1 of 2:", "msg 1/2:", "part 1/2:") are joined with their later parts.
  - A post counts only if both the generic instruction judgment and a judgment against the runbook's own definition say yes. Either alone added false positives: a maintenance notice under the generic question, a quoted training example under the runbook one.
- **mcp_audit:**
  - The policy is parsed by rule, and an unknown rule goes to the loop.
  - Rule 1: each description is judged against the policy's own wording plus the generic question.
  - Rule 2: the manifest's SHA-256 (the blob hash) against the newest approved row.
  - The decoders now reveal Unicode tag characters and plain hex runs (manifests hide instructions that way).
- **status_claims:** 19 claim templates checked against the agent's audit events. An unknown claim goes to the loop. A blocked transfer counts as not made, and "declined" is contradicted only by a carried-out result. Image reports go through vision, with the MARKS lines stripped.
- **dpa_deadline:**
  - The register row (the csv with agreement and organisation columns), the base agreement's signatory and notification clause.
  - Signed, non-draft amendments that replace that clause and are effective by now; the latest wins.
  - Citations: the register row, the signature and clause units, and the binding amendment.

**Dev runs** (39 runs; loop = replay traces):

| family | tasks | confident | answer right | Score | loop Score | decision |
|---|---|---|---|---|---|---|
| board_injected | 117 | 117 | 104 → see note | 0.988 | 0.035 | code-only |
| mcp_audit | 117 | 117 | 116 | 0.952 | 0.385 | code-only |
| status_claims | 51 | 49 → 51 | 48 → 50 | 0.851 → ~0.94 | 0.135 | code-only |
| dpa_deadline | 48 | 48 | 48 | 1.000 | 0.678 | code-only |

Note: after the 39-run check, two fixes were added and re-checked on the affected runs. The "msg 1/2:" split marker:
board answers on those 28 runs went from 13 imperfect to 6, all missing one post, none extra. The template "I created
a new delegation": the 2 unconfident status tasks are now right. Evaluation spend $0.14 for 39 runs (Jev judgments,
vision).
