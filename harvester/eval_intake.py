"""Evaluate harness intake (build ticket 05) on the development runs: family and task-kind accuracy, allowlist match
with the grader's task-kind mapping, Jev cost per run, and a confusion matrix.

    uv run harvester/eval_intake.py                 # all development runs (the 5 most recent runs are held out)
    uv run harvester/eval_intake.py --runs 3        # a cheaper subset
    uv run harvester/eval_intake.py --offline       # re-score cached Jev answers only (e.g. another --min-confidence)

Evaluation only: ground truth comes from harvester/data (expected answers via the grader's `task_kind`, and an
eval-only regex labeller over the dev request wording below). None of this is imported by the harness.
The classifier sees the resolved request text (the `request` column: the open ticket's text for ticket tasks, so photo
tickets are measured as if transcribed) plus, for inbox tasks, the inbox messages from the workspace.
Jev answers are cached in runs/intake_eval_cache.json (keyed by state + questions), so re-runs cost nothing.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "harvester"))

import harness.intake as I  # noqa: E402
from arena import llm  # noqa: E402
from harness.machine import Deadlines  # noqa: E402
from harness.pacing import Pacer  # noqa: E402
from replay.grade import AUTHORISED, task_kind  # noqa: E402
from replay.server import Store  # noqa: E402

DATA = ROOT / "harvester" / "data"
CACHE = ROOT / "runs" / "intake_eval_cache.json"
OUT = ROOT / "wayfinder" / "harness" / "build" / "05-intake-eval.md"
csv.field_size_limit(10**9)

# Eval-only labeller over the dev wording (first match wins). Covers every development request.
LABELS: list[tuple[str, str]] = [
    ("partner_draft", r"/drafts/"),
    ("breach_notification", r"breach notification for INC|personal-data breach notification"),
    ("containment", r"^(?:Full )?[Cc]ontain|[Cc]ontainment for|do the containment"),
    ("rotation", r"(?i)rotat"),
    ("inbox", r"/inbox/"),
    ("board_origin", r"acted on a message from the agents board|compromise back to its origin"),
    ("timeline", r"events.*(?:earliest|chronological)"),
    ("canaries", r"canar|honeytoken"),
    ("first_token", r"first secret the attackers|read first within the attack"),
    ("token_status", r"still valid|Status check on TOK"),
    ("status_claims", r"numbered claims"),
    ("delegation_chain", r"delegation chain"),
    ("lethal_trifecta", r"lethal trifecta|private data, untrusted input"),
    ("mcp_audit", r"MCP|/integrations/mcp/"),
    ("access_log", r"/logs/access/requests\.csv"),
    ("access_review", r"pending access requests|[Aa]ccess review"),
    ("leaked_tokens", r"[Ll]eak check|tokens were leaked"),
    ("board_injected", r"[Bb]oard triage|posts on the agents board|agents board export"),
    ("patient_zero", r"patient zero|entry point of|where INC-\S+ started"),
    ("compromised", r"compromised in incident|compromised agents|attacker-directed actions"),
    ("attacker_domains", r"indicator|screenshot|attacker domains"),
    ("dpa_deadline", r"agreement|notification deadline"),
    ("exfiltration", r"exfiltrat|actually (?:leave|receive)|customer records|[Bb]last radius"),
]


def label(request: str) -> str | None:
    for fam, rx in LABELS:
        if re.search(rx, request):
            return fam
    return None


def blob_text(sha: str) -> str:
    f = DATA / "blobs" / sha
    if not f.exists():
        f = next((DATA / "blobs").glob(sha + "*"))
    return f.read_bytes().decode("utf-8", "replace")


def load_tasks(n_runs: int | None) -> list[dict]:
    store = Store(DATA)
    dev = list(store.runs)[:-5]  # the 5 most recent harvested runs are the held-out set: never read here
    if n_runs:
        dev = dev[:n_runs]
    want = set(dev)
    rows = [r for r in csv.DictReader(open(DATA / "tasks.csv")) if r["run_id"] in want]
    for r in rows:
        files = json.loads((DATA / "runs" / r["run_id"] / "tasks" / r["task_id"] / "task.json").read_text())["files"]
        r["paths"] = files
        r["expected_answer"] = json.loads(r["expected_answer"]) if r["expected_answer"] else None
    return rows


def request_of(r: dict) -> I.Request:
    """What intake would resolve: the request text, plus inbox messages when the request points to the inbox."""
    files = r["paths"]
    return I.resolve_request(r["request"], list(files), lambda p: blob_text(files[p]))


def key(state: dict, qs: dict) -> str:
    return hashlib.sha256(json.dumps([state, qs], sort_keys=True).encode()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--runs", type=int, help="only the first N development runs")
    ap.add_argument("--offline", action="store_true", help="no Jev calls: cached answers only")
    ap.add_argument("--min-confidence", type=float, default=I.MIN_CONFIDENCE)
    ap.add_argument("--no-write", action="store_true", help=f"don't write {OUT.relative_to(ROOT)}")
    a = ap.parse_args()
    I.MIN_CONFIDENCE = a.min_confidence

    rows = load_tasks(a.runs)
    unlabelled = [r["request"][:120] for r in rows if not label(r["request"])]
    if unlabelled:
        print(f"{len(unlabelled)} requests without a label, e.g. {unlabelled[:3]}")
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    jobs: dict[str, tuple[dict, dict]] = {}
    for r in rows:
        req = request_of(r)
        state, qs = I.jev_state(req), I.questions(bool(req.messages))
        r["_key"], r["_with_message"] = key(state, qs), bool(req.messages)
        jobs.setdefault(r["_key"], (state, qs))
    todo = {k: v for k, v in jobs.items() if k not in cache or "cost" not in cache[k]}
    print(f"{len(rows)} tasks, {len(jobs)} distinct Jev states, {len(todo)} not cached")

    tracker = llm.CostTracker()
    if todo and not a.offline:
        pacer = Pacer(tracker, Deadlines(hard_s=3600, soft_s=3600))
        t0 = time.time()

        def call(item):
            k, (state, qs) = item
            one = llm.CostTracker()
            try:
                answers = pacer.call("jev", lambda t: llm.decide(state, qs, tracker=one, retries=0, timeout=t))
                tracker.add("jev", {"cost": one.total})
                return k, {"answers": answers, "cost": one.total}
            except Exception as e:  # noqa: BLE001 - record and score as 'other'
                return k, {"error": f"{type(e).__name__}: {e}"}
        with ThreadPoolExecutor(10) as ex:
            for k, v in ex.map(call, todo.items()):
                cache[k] = v
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache))
        print(f"Jev: {len(todo)} calls, ${tracker.total:.5f}, {time.time() - t0:.0f}s")
    costs = [cache[r["_key"]]["cost"] for r in rows if "cost" in cache.get(r["_key"], {})]
    per_task = sum(costs) / len(costs) if costs else None  # mean over tasks (not distinct states): what a run pays

    # --- score -------------------------------------------------------------------------------------------------
    conf = collections.Counter()
    fam_ok = kind_ok = allow_ok = n_ok = 0
    routed_loop = collections.Counter()
    kind_errors, allow_errors = collections.Counter(), collections.Counter()
    refusal_allow = collections.Counter()
    for r in rows:
        got = cache.get(r["_key"], {})
        it = I.interpret(got.get("answers") or {}, r["_with_message"])
        truth = label(r["request"]) or "?"
        conf[(truth, it.family)] += 1
        fam_ok += it.family == truth
        if not it.confident:
            routed_loop[truth] += 1
        exp_kind = task_kind(r["expected_outcome"], r["expected_answer"], r["request"])
        if exp_kind == "refusal":
            refusal_allow[(truth, it.kind)] += 1  # outcome is decided later; compose empties the allowlist
            continue
        n_ok += 1
        kind_ok += it.kind == exp_kind
        if it.kind != exp_kind:
            kind_errors[(truth, exp_kind, it.kind)] += 1
        want = sorted(AUTHORISED.get(exp_kind, ()))
        allow_ok += it.allowlist == want
        if it.allowlist != want:
            allow_errors[(truth, exp_kind, tuple(it.allowlist))] += 1

    n = len(rows)
    runs = len({r["run_id"] for r in rows})
    lines = [
        "# 05: Intake evaluation (development runs)",
        "",
        f"Generated by `uv run harvester/eval_intake.py` on {time.strftime('%Y-%m-%d %H:%M')}: {runs} development runs, "
        f"{n} tasks, {len(jobs)} distinct Jev states, MIN_CONFIDENCE {I.MIN_CONFIDENCE}.",
        "Ground truth: an eval-only regex labeller over the dev requests (family) and the grader's `task_kind` on the "
        "expected answers (kind, allowlist). Requests are the resolved text (photo tickets as if transcribed).",
        "",
        f"- **Family accuracy: {fam_ok / n:.3f}** ({fam_ok}/{n})",
        f"- **Task-kind accuracy (OK tasks): {kind_ok / max(1, n_ok):.3f}** ({kind_ok}/{n_ok})",
        f"- **Allowlist = grader mapping (OK tasks): {allow_ok / max(1, n_ok):.3f}** ({allow_ok}/{n_ok})",
        f"- Routed to the loop (other or below MIN_CONFIDENCE): {sum(routed_loop.values())} "
        f"({', '.join(f'{k} {v}' for k, v in routed_loop.most_common(8))})",
    ]
    if per_task is not None:
        lines.append(f"- Jev cost: ${per_task:.7f} per task → **${per_task * 48:.5f} per 48-task run** "
                     f"(one call per task; photo-ticket tasks counted as if transcribed)")
    lines += ["", "## Confusion (truth → predicted, off-diagonal only)", "", "| truth | predicted | tasks |", "|---|---|---|"]
    lines += [f"| {t} | {p} | {c} |" for (t, p), c in sorted(conf.items(), key=lambda kv: -kv[1]) if t != p]
    lines += ["", "## Per family", "", "| family | tasks | correct |", "|---|---|---|"]
    tot = collections.Counter()
    for (t, p), c in conf.items():
        tot[t] += c
    for fam in sorted(tot):
        lines.append(f"| {fam} | {tot[fam]} | {conf[(fam, fam)]} |")
    if kind_errors:
        lines += ["", "## Task-kind errors (family, expected, got)", ""]
        lines += [f"- {k}: {c}" for k, c in kind_errors.most_common(20)]
    if allow_errors:
        lines += ["", "## Allowlist mismatches (family, expected kind, got)", ""]
        lines += [f"- {k}: {c}" for k, c in allow_errors.most_common(20)]
    lines += ["", "## Refusal tasks: kind intake gave (compose empties the allowlist on a non-OK outcome)", ""]
    lines += [f"- {k}: {c}" for k, c in refusal_allow.most_common(20)]
    report = "\n".join(lines) + "\n"
    print(report)
    if not a.no_write:
        OUT.write_text(report)
        print(f"written: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
