"""Measure the code rotation scheduler (harness/guards.py, build ticket 07) against every expected rotation of the
development runs: rotation tasks (answer = the start) and containment answers (`rotate: [{token, at}]`).
Evaluation only (reads harvester/data; the 5 most recent runs are held out).

    uv run harvester/evals/eval_rotation.py

Standing orders are transcribed with the harness prompt (cached in runs/vision_eval_cache.json, shared with
eval_vision.py); settings come from harness.facts' parse_standing_order (Jev only where its regexes can't decide).
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "harvester"))

from arena import llm  # noqa: E402
from eval_intake import load_tasks  # noqa: E402
from harness import guards as G  # noqa: E402
from harness import vision as V  # noqa: E402
from harness.facts.instruction_judge import Judge  # noqa: E402
from harness.facts.runbook import facts_for  # noqa: E402
from harness.index import Index  # noqa: E402

DATA = ROOT / "harvester" / "data"
CACHE = ROOT / "runs" / "vision_eval_cache.json"
NEED = ("/AGENTS.md", "/runbook.md", "/fleet/tokens.csv", "/ops/change_calendar.csv", "/standing_order.jpg")


def transcript(sha: str, cache: dict, tracker: llm.CostTracker) -> str:
    if sha not in cache:
        data = (DATA / "blobs" / sha).read_bytes()
        msg = llm.chat([{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": V.data_url("/standing_order.jpg", data)}},
            {"type": "text", "text": V.PROMPT}]}], max_tokens=V.MAX_TOKENS, temperature=0.0, tracker=tracker)
        cache[sha] = {"text": (msg.get("content") or "").strip()}
    return cache[sha]["text"]


def main() -> int:
    rows = load_tasks(None)
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    tracker = llm.CostTracker()
    judge = Judge(decide=lambda st, q, **kw: llm.decide(st, q, tracker=tracker))
    cases = []
    for r in rows:
        exp = r["expected_answer"]
        if r["expected_outcome"] != "OK":
            continue
        if isinstance(exp, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:00:00Z", exp) and re.search("rotat", r["request"], re.I):
            tok = re.search(r"\bTOK-[A-Z]+-\d+\b", r["request"])
            if tok:
                cases.append((r, tok.group(0), exp, "rotation"))
        elif isinstance(exp, dict) and isinstance(exp.get("rotate"), list):
            for x in exp["rotate"]:
                cases.append((r, x["token"], x["at"], "containment"))
    res = collections.Counter()
    misses = []
    for r, tok, want, kind in cases:
        files = {p: r["paths"][p] for p in NEED if p in r["paths"]}
        idx = Index()
        for p, h in files.items():
            idx.add(p, h, (DATA / "blobs" / h).read_bytes())
        idx.files["t"] = files
        so = transcript(files["/standing_order.jpg"], cache, tracker) if "/standing_order.jpg" in files else None
        f = facts_for(idx.view("t"), judge, {"/standing_order.jpg": so} if so else {})
        got, why = G.schedule_rotation(f, tok)
        ok = got == want
        res[(kind, ok)] += 1
        if not ok and len(misses) < 10:
            misses.append((r["run_id"], r["task_id"], tok, want, got, why[:2], vars(f.settings().value)))
    CACHE.write_text(json.dumps(cache))
    for kind in ("rotation", "containment"):
        n = res[(kind, True)] + res[(kind, False)]
        print(f"{kind}: {res[(kind, True)]}/{n} exact")
    for m in misses:
        print("  miss:", m)
    print(f"model spend: ${tracker.total:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
