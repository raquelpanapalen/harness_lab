"""Per-task score differences between hub replay results and a theme's results on the same runs.

    python3 harvester/synth/compare.py port [--hub-dir harvester/replay_results]
"""
import argparse
import json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("theme")
ap.add_argument("--hub-dir", default="harvester/replay_results")
a = ap.parse_args()
rows = []
for f in sorted(Path(f"harvester/replay_results/{a.theme}").glob("rp*_run_*.json")):
    src = f.stem.rsplit(f"_{a.theme}", 1)[0]                       # rp001_run_X
    hub = sorted(Path(a.hub_dir).glob(f"rp*_{src.split('_', 1)[1]}.json"))
    if not hub:
        continue
    ht = {t["task_id"]: t for t in json.load(open(hub[-1]))["tasks"]}
    for t in json.load(open(f))["tasks"]:
        h = ht[t["task_id"]]
        sh = (h.get("grade") or {}).get("score") or 0
        sp = (t.get("grade") or {}).get("score") or 0
        if abs(sp - sh) > 1e-9:
            sub = json.dumps((t.get("submission") or {}).get("answer"), ensure_ascii=False)[:220]
            rows.append((sp - sh, src.split("_", 1)[1], t["task_id"], t["level"], (t.get("grade") or {}).get("replay_kind"),
                         sh, sp, " | ".join((t.get("grade") or {}).get("feedback", [])[:4]),
                         json.dumps(t["expected_answer"], ensure_ascii=False)[:220], sub,
                         (t.get("request") or t["instruction"])[:120]))
rows.sort()
print(f"{len(rows)} tasks differ; lost {sum(r[0] for r in rows if r[0] < 0):.2f}, gained {sum(r[0] for r in rows if r[0] > 0):.2f}")
for d, rid, tid, lv, k, sh, sp, fb, exp, sub, req in rows:
    print(f"\n{d:+.2f} {rid} {tid} L{lv} {k}: hub {sh:.2f} -> {a.theme} {sp:.2f}  [{fb}]\n   req: {req}\n   exp: {exp}\n   sub: {sub}")
