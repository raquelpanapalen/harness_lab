"""Summarize a harvested dataset: what kinds of tasks exist, by level and family, plus a flat tasks.csv.

    uv run harvester/summarize.py                 # reads harvester/data
    uv run harvester/summarize.py --out other/dir

The format of the revealed answers (GET /runs/{id} after finish) isn't documented in detail, so expected
outcome/answer are found best-effort: any dict carrying this task's id and an outcome/answer-like key.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Rough families by instruction wording. Extend once you've seen real tasks.
FAMILIES = {
    "inbox/message": r"\binbox|message|e-?mail|reply|sender",
    "containment/action": r"contain|isolat|quarantin|block|revoke|disable|rotat",
    "logs/audit": r"\blog|audit|event|login|access",
    "tokens/credentials": r"token|credential|secret|key\b|password",
    "agents/identity": r"\bagent|which (?:agent|account|user)",
    "threat intel": r"threat|ioc|indicator|domain|ip\b|advisory",
    "counting/aggregation": r"how many|count|total|sum|average|most|least|top",
    "documents": r"\.pdf|\.docx|\.xlsx|report|spreadsheet|document",
}

OUTCOME_KEYS = ("expected_outcome", "outcome")
ANSWER_KEYS = ("expected_answer", "answer")


def find_expected(revealed, task_id: str) -> dict:
    """Search the revealed run JSON for the entry describing `task_id`."""
    hits: list[dict] = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("task_id") == task_id:
                hits.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(revealed)
    out: dict = {}
    for h in hits:
        flat = dict(h)
        for k in ("expected", "solution", "gold"):
            if isinstance(h.get(k), dict):
                flat.update({f"expected_{kk}" if kk in ("outcome", "answer", "sources") else kk: vv
                             for kk, vv in h[k].items()})
        for k in OUTCOME_KEYS:
            if k in flat and "outcome" not in out:
                out["outcome"] = flat[k]
        for k in ANSWER_KEYS:
            if k in flat and "answer" not in out:
                out["answer"] = flat[k]
        if "request" in flat and "request" not in out:
            out["request"] = flat["request"]
        for k in ("expected_sources", "sources"):
            if k in flat and "sources" not in out:
                out["sources"] = flat[k]
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--out", type=Path, default=HERE / "data")
    a = p.parse_args()

    rows = []
    for rdir in sorted((a.out / "runs").glob("*")):
        if not (rdir / "run.json").exists():
            continue
        run = json.loads((rdir / "run.json").read_text())
        revealed = json.loads((rdir / "revealed.json").read_text()) if (rdir / "revealed.json").exists() else {}
        for t in run.get("tasks", []):
            exp = find_expected(revealed, t["task_id"])
            # The instruction is often just "work the open ticket"; the revealed `request` is the real question.
            request = exp.get("request") or t["instruction"]
            fams = [f for f, rx in FAMILIES.items() if re.search(rx, request, re.I)] or ["other"]
            tj = rdir / "tasks" / t["task_id"] / "task.json"
            nfiles = len(json.loads(tj.read_text()).get("files", {})) if tj.exists() else None
            rows.append({"run_id": run["run_id"], "seed": run.get("seed"), "theme": run.get("theme"),
                         "variation": run.get("variation"), "task_id": t["task_id"], "level": t.get("level"),
                         "families": "|".join(fams), "files": nfiles,
                         "expected_outcome": exp.get("outcome"),
                         "expected_answer": json.dumps(exp.get("answer"), ensure_ascii=False) if "answer" in exp else "",
                         "expected_sources": json.dumps(exp.get("sources"), ensure_ascii=False) if "sources" in exp else "",
                         "instruction": t["instruction"],
                         "request": request,
                         "finished": (rdir / "finish.json").exists()})
    if not rows:
        print(f"no harvested runs in {a.out / 'runs'}")
        return 1

    with (a.out / "tasks.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    runs = {r["run_id"] for r in rows}
    print(f"{len(rows)} tasks from {len(runs)} runs  ->  {a.out / 'tasks.csv'}\n")
    print("themes:   ", dict(Counter(r["theme"] for r in rows)))
    print("levels:   ", dict(sorted(Counter(r["level"] for r in rows).items(), key=lambda kv: str(kv[0]))))
    print("outcomes: ", dict(Counter(r["expected_outcome"] for r in rows)))
    unfinished = sorted({r["run_id"] for r in rows if not r["finished"]})
    if unfinished:
        print("unfinished runs (no answers yet, use --resume):", unfinished)

    fam = Counter(f for r in rows for f in r["families"].split("|"))
    print("\nfamilies (a task can be in several):")
    for f, n in fam.most_common():
        print(f"  {n:4d}  {f}")

    print("\nexample instructions per level:")
    by_level = defaultdict(list)
    for r in rows:
        by_level[r["level"]].append(r)
    for lvl in sorted(by_level, key=str):
        print(f"  level {lvl}:")
        for r in by_level[lvl][:3]:
            print(f"    [{r['expected_outcome'] or '?'}] {r['request'][:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
