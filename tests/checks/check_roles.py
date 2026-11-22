"""The file-role resolver (harness/roles.py) against harvested runs: on hub-layout data every role must resolve to the
file v1 found by its hard-coded path; on a reskinned layout (`--data harvester/data/synth/layout-dev`) every role must
resolve at all. Evaluation only: reads harvester/data. Held-out runs skipped.

    uv run tests/checks/check_roles.py [--runs N] [--data DIR] [--hub]
"""
from __future__ import annotations

import argparse
import collections
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "checks"))

from check_index import DATA, build, dev_runs  # noqa: E402
from harness.index import ext  # noqa: E402
from harness.roles import roles_of  # noqa: E402

# v1's rules (as of tag v1.1), for comparison on hub data only
V1 = {
    "audit": lambda ps: sorted(p for p in ps if p.startswith("/logs/audit/") and ext(p) == "jsonl"),
    "archive": lambda ps: sorted(p for p in ps if p.startswith("/logs/archive/") and ext(p) == "csv"),
    "board": lambda ps: sorted(p for p in ps if p.startswith("/board/") and ext(p) == "jsonl"),
    "changes": lambda ps: next((p for p in sorted(ps) if p.startswith("/fleet/") and "change" in p.lower()
                                and ext(p) == "md"), None),
    "artifact_index": lambda ps: next((p for p in sorted(ps) if p.startswith("/artifacts/") and ext(p) == "md"
                                       and "index" in p.lower()), None),
    "containment_states": lambda ps: sorted(p for p in ps if p.startswith("/plans/") and ext(p) == "json"),
    "mcp_dir": lambda ps: "/integrations/mcp/" if any(p.startswith("/integrations/mcp/") for p in ps) else None,
    "signed_order": lambda ps: "/standing_order.jpg" if "/standing_order.jpg" in ps else None,
    "runbook": lambda ps: "/runbook.md" if "/runbook.md" in ps else None,
    "access_log": lambda ps: next((p for p in sorted(ps) if p.startswith("/logs/access/") and "request" in p.lower()),
                                  None),
    "access_log_policy": lambda ps: next((p for p in sorted(ps) if p.startswith("/logs/access/")
                                          and re.search(r"polic", p, re.I)), None),
    "pending_access": lambda ps: next((p for p in sorted(ps) if "pending" in p.lower() and p.endswith(".csv")), None),
}
FIRST = ["/AGENTS.md", "/runbook.md", "/standing_order.md", "/contacts.csv", "/fleet/agents.csv",
         "/fleet/registry_export.json", "/fleet/changes.md", "/fleet/tokens.csv", "/logs/audit/", "/logs/archive/",
         "/board/", "/fleet/token_fingerprints.csv"]


def resolved(r, name):
    if name in ("audit", "archive", "board", "containment_states"):
        return sorted(getattr(r, name))
    if name in ("changes", "artifact_index", "registry", "contacts", "access_log", "access_log_policy",
                "pending_access"):
        return r.one(name)
    return getattr(r, name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=4)
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--hub", action="store_true", help="also compare with v1's path rules (hub layout only)")
    a = ap.parse_args()
    data = Path(a.data)
    runs = dev_runs() if data == DATA else sorted(p for p in (data / "runs").iterdir() if (p / "manifest.json").exists())
    bad, seen = collections.Counter(), collections.Counter()
    for rdir in runs[:a.runs]:
        idx = build(rdir)
        for tid in sorted(idx.files):
            v = idx.view(tid)
            r = roles_of(v)
            ps = set(v.paths())
            names = list(V1) + ["registry", "contacts", "order_copy", "roster"]
            for n in names:
                got = resolved(r, n)
                seen[n] += 1
                if a.hub and n in V1:
                    want = V1[n](ps)
                    if got != want:
                        bad[n] += 1
                        if bad[n] <= 3:
                            print(f"  {rdir.name} {tid} {n}: roles {got!r} vs v1 {want!r}")
                elif got in (None, []):
                    bad[n] += 1
                    if bad[n] <= 3:
                        print(f"  {rdir.name} {tid} {n}: not resolved (candidates {r.all(n) if n in r.by_tag else '-'})")
            if a.hub:
                want = [p for p in FIRST if p in ps or any(q.startswith(p) for q in ps if p.endswith("/"))]
                got = r.search_order()
                if got != want:
                    bad["search_order"] += 1
                    if bad["search_order"] <= 3:
                        print(f"  {rdir.name} {tid} search order: {got} vs {want}")
        print(f"{rdir.name}: {len(idx.files)} tasks checked", flush=True)
    for n in sorted(seen):
        print(f"{n:20s} {seen[n] - bad[n]}/{seen[n]}")
    if bad.get("search_order"):
        print(f"search_order        {bad['search_order']} tasks differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
