"""Break down replay results by level, task kind and file family, and score runs on the leaderboard metric.

    uv run harvester/report.py harvester/replay_results/baseline/*.json
    uv run harvester/report.py --worst 15 harvester/replay_results/baseline/*.json   # also list the lowest-scoring tasks
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Leaderboard (arena.ai-mission.eu/leaderboard, read 2026-11-17): Score = A * (0.5 + 0.5 * C), with C on a log scale
# from 1 at the cheapest run to 0 at the priciest. Fixed to the event's range so our runs compare with the board.
C_CHEAP, C_PRICEY = 0.049, 1.135
BOARD = [("AIT", 0.750), ("Donaufriends", 0.524), ("Adrian Kaiser", 0.455)]


def cost_score(cost: float) -> float:
    if cost <= C_CHEAP:
        return 1.0
    if cost >= C_PRICEY:
        return 0.0
    return math.log(C_PRICEY / cost) / math.log(C_PRICEY / C_CHEAP)


def run_cost(run_id: str) -> float | None:
    """Model spend of a run: the largest cumulative `cost_total` in its traces (runs/<run_id>/*.jsonl)."""
    best = None
    for f in (ROOT / "runs" / run_id).glob("*.jsonl"):
        for line in f.open():
            c = json.loads(line).get("cost_total")
            if c is not None and (best is None or c > best):
                best = c
    return best


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
    runs = []
    for p in a.results:
        r = json.loads(p.read_text())
        acc = sum((t.get("grade") or {}).get("score", 0.0) for t in r["tasks"]) / len(r["tasks"])
        runs.append((r["run_id"], acc, run_cost(r["run_id"])))
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
    print(f"\nleaderboard metric: Score = A * (0.5 + 0.5 * C), C log-scaled ${C_CHEAP} -> 1 .. ${C_PRICEY} -> 0")
    print(f"{'run':<28} {'A':>6} {'cost':>8} {'C':>6} {'Score':>6}")
    scored = []
    for rid, acc, cost in runs:
        if cost is None:
            print(f"{rid:<28} {acc:>6.3f} {'?':>8}   (no traces with cost_total in runs/{rid}/)")
            continue
        c = cost_score(cost)
        scored.append((acc, cost, c, acc * (0.5 + 0.5 * c)))
        print(f"{rid:<28} {acc:>6.3f} {cost:>8.4f} {c:>6.3f} {scored[-1][3]:>6.3f}")
    if len(scored) > 1:
        m = [sum(x[i] for x in scored) / len(scored) for i in range(4)]
        print(f"{'mean':<28} {m[0]:>6.3f} {m[1]:>8.4f} {m[2]:>6.3f} {m[3]:>6.3f}")
    print("board: " + ", ".join(f"{n} {v:.3f}" for n, v in BOARD))
    if a.worst:
        print(f"\nlowest {a.worst}:")
        for t in sorted(tasks, key=lambda t: t["score"])[: a.worst]:
            print(f"  {t['run'][-8:]} {t['task_id']} L{t['level']} {t['kind']:<22} {t['score']:.2f}  {t['request']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
