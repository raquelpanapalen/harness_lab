# 11: Solver wave 2

**What to build:** Solvers for leaked tokens, access-log policy violations, access review, delegation chain and lethal trifecta, each reading its rule settings from the workspace.

**Blocked by:** 10

**Status:** done

- [x] Per-family Score: solver vs. loop on development runs
- [x] Enable/disable decision per family recorded

Spec: [SPEC.md](../SPEC.md)

## Result

`harness/solvers/wave2.py`. Every rule is read from the task's workspace:
- **leaked_tokens:** the fingerprint csv's `sha256_12` of every candidate value in the named material (the board when the request names it), decoded first; adjacent tokens are also joined, for values wrapped over two lines or split over posts.
- **access_log:** POLICY.md parsed clause by clause, and an unknown clause goes to the loop. A ticket exempts a request only when it is the request's own ticket: actor, resource and window match, and it isn't revoked. Emergency windows match the exact actor, resource and action.
- **access_review:** quarantined or compromised agent, filed by someone other than the roster owner, or a new lethal trifecta after applying the request alone to the current permission graph.
- **delegation_chain:** BFS over current permissions.
- **lethal_trifecta:** transitive closure over current permissions, or the registry export when the request asks for it.

**Citations** (tuned on hit counts only, never by reading expected answers):
- permission answers cite the registry export by path plus every change after the export;
- access review cites the denied requests plus the changes that touch their agents;
- leaked tokens cite the leaking line and the fingerprint row.

**Dev runs** (`tests/check_solvers.py`, 39 runs, `replay.grade`; loop = replay traces rp001–rp010, the same families):

| family | tasks | confident | answer right | Score | loop Score | enabled |
|---|---|---|---|---|---|---|
| leaked_tokens | 117 | 117 | 117 | 1.000 | 0.052 | yes |
| access_log | 117 | 117 | 117 | 1.000 | 0.055 | yes |
| access_review | 117 | 117 | 117 | 0.989 | 0.297 | yes |
| delegation_chain | 24 | 24 | 24 | 1.000 | 0.000 | yes |
| lethal_trifecta | 43 | 43 | 43 | 0.958 | 0.067 | yes |

All five are enabled. Two bugs found on the way:
- `facts_for` shared one Facts across tasks, so `/logs/access`, `/intel` and `/board` were read from another task's copy. Each task now reads through its own view (f18a39c).
- The checker labels families by request regex (evaluation only).
