"""Compare the replay tools with every real tool response recorded by the harvester (runs/*/probes.json and the
`tree` recordings). Exit code 1 on any mismatch.

    uv run harvester/replay/check_probes.py [-v]
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from replay import tools as T  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"


def first_diff(a, b, path="$"):
    if type(a) is not type(b):
        return f"{path}: type {type(a).__name__} vs {type(b).__name__}: {str(a)[:150]!r} vs {str(b)[:150]!r}"
    if isinstance(a, dict):
        if set(a) != set(b):
            return f"{path}: keys real-only {sorted(set(a) - set(b))} mine-only {sorted(set(b) - set(a))}"
        for k in a:
            if (d := first_diff(a[k], b[k], f"{path}.{k}")):
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: len {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            if (d := first_diff(x, y, f"{path}[{i}]")):
                return d
        return None
    if a != b:
        if isinstance(a, str) and "\n" in a:
            for i, (x, y) in enumerate(zip(a.split("\n"), b.split("\n"))):
                if x != y:
                    return f"{path} line {i}: {x[:150]!r} vs {y[:150]!r}"
        return f"{path}: {str(a)[:200]!r} vs {str(b)[:200]!r}"
    return None


def main() -> int:
    verbose = "-v" in sys.argv
    page_counts: dict = {}
    stats = collections.Counter()
    bad = collections.defaultdict(list)
    for rdir in sorted((DATA / "runs").iterdir()):
        pj = rdir / "probes.json"
        if not pj.exists():
            continue
        pr = json.loads(pj.read_text())
        tid = pr["task_id"]
        files = {t["task_id"]: t["files"] for t in json.loads((rdir / "manifest.json").read_text())["tasks"]}[tid]
        rec = json.loads((rdir / "tasks" / tid / "tools.json").read_text()) if (rdir / "tasks" / tid / "tools.json").exists() else {}
        intel = json.loads((rdir / "intel.json").read_text()) if (rdir / "intel.json").exists() else {}
        ws = T.Workspace(files, DATA / "blobs", page_counts, intel, (rec.get("tree") or {}).get("result"))
        for p in pr["probes"]:
            if "response" not in p:
                continue
            real = p["response"]
            if p["tool"] == "threat_intel" and real.get("ok") and p["args"].get("indicator", "").lower() not in ws.intel:
                continue  # an unknown indicator: the replay can't know the feed's answer
            mine = T.call(ws, p["tool"], dict(p["args"]))
            key = p["tool"]
            stats[key] += 1
            if (d := first_diff(real, mine)):
                bad[key].append((rdir.name, p["args"], d))
    for tool in sorted(stats):
        n_bad = len(bad[tool])
        print(f"{tool:<16} {stats[tool] - n_bad:>3}/{stats[tool]} identical")
        for run, args, d in bad[tool][: (99 if verbose else 3)]:
            print(f"    {run} {json.dumps(args)[:80]}\n        {d}")
    return 1 if any(bad.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
