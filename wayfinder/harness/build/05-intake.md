# 05: Intake

**What to build:** Each task gets one Jev call that picks its family from a catalogue of written family descriptions (never dev wording), plus task kind and the allowlist of operations; low confidence or 'other' routes to the loop. The workspace `/AGENTS.md` and the operator files it names are read as the only trusted text, and their hard rules, outcomes and source tiers are prepared for the loop and compose prompts.

**Blocked by:** 02

**Status:** done

- [x] Family and task-kind accuracy measured on development tasks (target ≥ 95 %), confusion matrix saved
- [x] Allowlist matches the grader's task-kind mapping on development tasks
- [x] Intake costs ≤ ~~$0.001~~ $0.002 per run in Jev spend (target raised by the human, 2026-11-17: see Done)

Spec: [SPEC.md](../SPEC.md)

## Done

- `harness/intake.py`:
  - **Request:** the instruction; for a ticket-queue task, the text ticket with `status: open` (front matter, else a
    `status:` line); photo tickets are listed in `unread_images` and wait for a `transcribe` hook (build ticket 08);
    for an inbox task, the instruction plus the inbox messages (data, classified, never obeyed).
  - **Trusted rules:** `/AGENTS.md` verbatim (condensed by code to the rules/outcomes/tiers/instructions sections above
    6,000 chars); operator files = the paths in its "operator files" sentence (`/AGENTS.md`, `/runbook.md` on dev).
  - **One Jev call:** a `choice` over 23 families plus "other", described in our own words. A request that hands us an
    inbox message is an inbox task by structure, and the call asks only which action the message wants (block,
    quarantine, send, none): with the family question, Jev filed 32 inbox tasks as containment.
  - **Kind and allowlist:** kind from the family (containment, rotation, notify, draft; inbox: `action:<op>`, or
    answer for "none"); allowlist = the grader's task-kind mapping, empty unless the probability is ≥ 0.5.
- The INTAKE state fills the task record (request, source, inbox, family, confidence, kind, allowlist, rules,
  operator files) and takes the answer shape from the resolved request. SOLVE still routes everything to the loop.
- **Evaluation** (`uv run harvester/eval_intake.py`, report in [05-intake-eval.md](05-intake-eval.md)): 39 dev runs,
  1,872 tasks. Family 1.000, task kind 1.000 and allowlist = grader mapping 1.000 (1,803 OK tasks); no task below the
  confidence threshold (lowest correct 0.62, median 1.0). Ground truth: an eval-only regex labeller over the dev
  requests and the grader's `task_kind`. Requests are the resolved text, so photo tickets are measured as if
  transcribed.
- **Cost:** $0.0018 per 48-task run. Jev bills ~330 tokens of fixed overhead per call plus ~22 per option, so a
  24-option catalogue can't reach $0.001. The human accepted ≤ $0.002 (~1.8 % of the $0.10 run budget, Score effect
  ≈ −0.001) over a code prefilter to the top-k families (risk of misrouting unseen final wording).
- Replay rp009_run_aT-eldEdFrOG (`main.py --harness lab`): 48/48 submitted. 36 tasks classified, $0.0013 of Jev; 12
  photo tickets stay "other" until ticket 08. Rules on 48/48. Fallback answer shapes now come from the ticket text.
- Tests: `tests/test_lab_intake.py` (ticket, photo-ticket and inbox resolution, operator files, condensing, answer
  interpretation incl. refusals and low confidence, allowlists equal the grader mapping, INTAKE fills the record).
  Machine and pacing tests stub `Pacer.decide`. A missing `OPENROUTER_API_KEY` is now a non-retryable error (401).
