"""Break down replay results by level, task kind and file family.

    uv run harvester/report.py harvester/replay_results/baseline/*.json
    uv run harvester/report.py --worst 15 harvester/replay_results/baseline/*.json   # also list the lowest-scoring tasks
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_meta() -> dict[tuple[str, str], dict]:
    path = HERE / "data" / "tasks.csv"
    if not path.exists():
        return {}
    with path.open() as f:
        return {(r["run_id"], r["task_id"]): r for r in csv.DictReader(f)}


def row(name: str, xs: list[dict]) -> str:
    n = len(xs)
    acc = sum(x["score"] for x in xs) / n
    ans = sum(x["answer_score"] for x in xs) / n
    con = sum(x["constraints_ok"] for x in xs) / n
    src = sum(x["sources_score"] for x in xs) / n
    perfect = sum(x["score"] >= 0.999 for x in xs)
    zero = sum(x["score"] == 0 for x in xs)
    return f"{name:<34} {n:>4} {acc:>6.3f} {ans:>6.3f} {con:>6.2f} {src:>6.2f} {perfect:>5} {zero:>5}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+", type=Path)
    ap.add_argument("--worst", type=int, default=0)
    a = ap.parse_args()
    meta = load_meta()
    tasks: list[dict] = []
    for p in a.results:
        r = json.loads(p.read_text())
        src = r.get("replay_of") or r["run_id"].split("_", 1)[1]
        for t in r["tasks"]:
            g = t.get("grade") or {}
            m = meta.get((src, t["task_id"]), {})
            tasks.append({
                "run": r["run_id"], "task_id": t["task_id"], "level": t.get("level"),
                "kind": g.get("replay_kind") or "unsubmitted",
                "family": (m.get("families") or "?").split("|")[0],
                "outcome": t.get("expected_outcome"),
                "request": (m.get("request") or t.get("instruction") or "")[:110],
                "score": g.get("score", 0.0), "answer_score": g.get("answer_score", 0.0),
                "constraints_ok": bool(g.get("constraints_ok", False)),
                "sources_score": g.get("sources_score", 0.0), "feedback": " | ".join(g.get("feedback", [])),
            })
    head = f"{'':<34} {'n':>4} {'score':>6} {'answer':>6} {'constr':>6} {'srcs':>6} {'=1':>5} {'=0':>5}"
    print(f"{len(a.results)} runs, {len(tasks)} tasks\n")
    for title, key in (("level", "level"), ("expected outcome", "outcome"), ("task kind", "kind"),
                       ("first file family", "family")):
        groups: dict[str, list[dict]] = defaultdict(list)
        for t in tasks:
            groups[str(t[key])].append(t)
        print(f"by {title}\n{head}")
        for k in sorted(groups, key=lambda k: -len(groups[k])):
            print(row(k, groups[k]))
        print()
    print(row("ALL", tasks))
    if a.worst:
        print(f"\nlowest {a.worst}:")
        for t in sorted(tasks, key=lambda t: t["score"])[: a.worst]:
            print(f"  {t['run'][-8:]} {t['task_id']} L{t['level']} {t['kind']:<22} {t['score']:.2f}  {t['request']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
