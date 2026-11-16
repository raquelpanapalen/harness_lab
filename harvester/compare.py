"""Compare a harness run on the REAL arena with runs of the same seed on the replay.

    uv run harvester/compare.py REAL_RUN_ID harvester/replay_results/**/rp*_<src>.json ...

1. Grader fidelity: every real submission is re-graded by the local grader (answer + citation tasks; action tasks
   need the real workspace state) and compared with the real grade of the same submission.
2. Agent level: real per-task scores vs replay per-task scores of the same tasks (matched by level, expected
   outcome/answer, then request wording). Replay-vs-replay differences give the model's run-to-run noise.
3. Real responses of state-changing tools found in the run's traces (runs/<run_id>/*.jsonl) are saved, so the
   replay's guessed formats can be checked.
Needs the real ARENA_BASE_URL / ARENA_API_KEY (.env) for step 1's GET /runs. Read-only on the arena.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from arena.client import ArenaClient  # noqa: E402
from replay.grade import answer_mode, compare, grade_sources, task_kind  # noqa: E402

OUT = HERE / "data" / "compare"
ACTION_TOOLS = {"containment", "send_message", "write"}


def words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9][a-z0-9:_@.-]{2,}", (s or "").lower()))


def key(t: dict) -> str:
    return json.dumps([str(t.get("level")), t.get("expected_outcome"), t.get("expected_answer")], sort_keys=True)


def match(a: list[dict], b: list[dict]) -> dict[str, str]:
    """a task_id -> b task_id, one to one: identical (level, outcome, answer) first, then shared request words."""
    out, used = {}, set()
    by_key = defaultdict(list)
    for t in b:
        by_key[key(t)].append(t["task_id"])
    rest = []
    for t in a:
        cands = [x for x in by_key.get(key(t), []) if x not in used]
        if len(cands) == 1:
            out[t["task_id"]] = cands[0]
            used.add(cands[0])
        else:
            rest.append(t)
    pairs = []
    for t in rest:
        wa = words(t.get("request") or t.get("instruction"))
        for u in b:
            if u["task_id"] in used or str(u.get("level")) != str(t.get("level")):
                continue
            wb = words(u.get("request") or u.get("instruction"))
            pairs.append((len(wa & wb) / (len(wa | wb) or 1), t["task_id"], u["task_id"]))
    for _, x, y in sorted(pairs, reverse=True):
        if x not in out and y not in used:
            out[x] = y
            used.add(y)
    return out


def regrade(t: dict) -> dict | None:
    """Local grade of the real submission, for tasks whose grade depends only on the submission."""
    sub = t.get("submission") or {}
    kind = task_kind(t["expected_outcome"], t["expected_answer"])
    if not sub or kind not in ("answer", "refusal"):
        return None
    ok = sub.get("outcome") == t["expected_outcome"]
    mode = answer_mode(t.get("request") or t.get("instruction") or "", t["expected_outcome"])
    if t["expected_outcome"] == "UNSUPPORTED":
        a = 1.0 if ok else 0.0
    else:
        a = compare(sub.get("answer"), t["expected_answer"], mode=mode) if ok else 0.0
    hit, n, s = grade_sources(sub.get("sources") or [], t.get("expected_sources") or [])
    return {"answer_score": a, "sources_score": s, "score": a * (0.7 + 0.3 * s), "kind": kind}


def action_responses(run_id: str) -> list[dict]:
    out = []
    for f in sorted((ROOT / "runs" / run_id).glob("t*.jsonl")):
        for line in f.open():
            e = json.loads(line)
            if e.get("event") == "tool" and e.get("name") in ACTION_TOOLS:
                out.append({"task": f.stem, "tool": e["name"], "args": e.get("args"), "output": e.get("output")})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("real_run")
    ap.add_argument("replays", nargs="+", type=Path, help="replay result files of the same seed")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    real = ArenaClient().get_run(a.real_run)
    (OUT / f"{a.real_run}.json").write_text(json.dumps(real, indent=1))
    rt = real["tasks"]
    print(f"real {a.real_run} seed={real.get('seed')}: accuracy {real.get('accuracy')}, "
          f"{sum(bool(t.get('submitted')) for t in rt)}/{len(rt)} submitted")

    # 1. grader fidelity on the same submissions
    diffs, n = [], 0
    for t in rt:
        g, lg = t.get("grade") or {}, regrade(t)
        if not lg or not g:
            continue
        n += 1
        d = abs(lg["score"] - g.get("score", 0))
        if d > 0.011:
            diffs.append((d, t["task_id"], g.get("score"), lg["score"], g.get("answer_score"), lg["answer_score"],
                          g.get("sources_score"), round(lg["sources_score"], 4), (t.get("request") or "")[:90]))
    print(f"\n1. grader: local re-grade of the real submissions agrees on {n - len(diffs)}/{n} answer/refusal tasks")
    for d in sorted(diffs, reverse=True):
        print(f"   {d[1]} real {d[2]} local {d[3]:.3f} | answer {d[4]} vs {d[5]:.3f} | sources {d[6]} vs {d[7]} | {d[8]}")

    # 2. agent level
    reps = [json.loads(p.read_text()) for p in a.replays]
    print(f"\n2. scores on the same tasks (real vs {len(reps)} replay run(s) of seed {real.get('seed')})")
    print(f"   real   {real.get('accuracy')}")
    maps = []
    for r in reps:
        print(f"   replay {r.get('accuracy')}  {r['run_id']}")
        maps.append(match(rt, r["tasks"]))
    rows, by_kind = [], defaultdict(lambda: defaultdict(list))
    for t in rt:
        rs = []
        for r, m in zip(reps, maps):
            u = next((x for x in r["tasks"] if x["task_id"] == m.get(t["task_id"])), None)
            rs.append(((u or {}).get("grade") or {}).get("score", 0.0))
        s = (t.get("grade") or {}).get("score", 0.0)
        kind = task_kind(t["expected_outcome"], t["expected_answer"])
        by_kind[kind]["real"].append(s)
        by_kind[kind]["replay"].append(st.mean(rs))
        rows.append((t["task_id"], kind, s, rs, (t.get("request") or t["instruction"])[:80]))
    print(f"\n   {'kind':<24} {'n':>3} {'real':>6} {'replay':>6}")
    for k, v in sorted(by_kind.items(), key=lambda kv: -len(kv[1]["real"])):
        print(f"   {k:<24} {len(v['real']):>3} {st.mean(v['real']):>6.3f} {st.mean(v['replay']):>6.3f}")
    if len(reps) >= 2:
        noise = [abs(r[3][0] - r[3][1]) for r in rows]
        print(f"\n   per-task |replay A - replay B| (noise): mean {st.mean(noise):.3f}")
    gap = [abs(r[2] - st.mean(r[3])) for r in rows]
    print(f"   per-task |real - replay mean|:          mean {st.mean(gap):.3f}")

    # 3. state-changing tool responses
    acts = action_responses(a.real_run)
    (OUT / f"{a.real_run}.actions.json").write_text(json.dumps(acts, indent=1))
    print(f"\n3. {len(acts)} real state-changing tool responses saved to {OUT / (a.real_run + '.actions.json')}")
    for x in acts[:8]:
        print(f"   {x['task']} {x['tool']} {json.dumps(x['args'])[:70]} -> {str(x['output'])[:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
