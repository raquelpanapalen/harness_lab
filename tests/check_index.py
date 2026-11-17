"""Check the workspace index (build ticket 04) against harvested development runs. Evaluation only: it reads
harvester/data (recorded server responses, expected citations), which the harness itself never does. The 5 held-out
runs (newest, as `replay/server.py --only-runs 5` picks them) are skipped.

    uv run tests/check_index.py probes      # local tree/list/read/search/query_logs == recorded server responses
    uv run tests/check_index.py citations   # share of expected citations that resolve to an evidence unit
    uv run tests/check_index.py untrusted   # untrusted-flag recall / false positives (planted text = diff vs base)
    uv run tests/check_index.py all [-v] [--runs N]
"""
from __future__ import annotations

import collections
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "harvester"))

from harness import localtools  # noqa: E402
from harness.index import Index, _match  # noqa: E402
from replay.check_probes import first_diff  # noqa: E402

DATA = ROOT / "harvester" / "data"
HELD_OUT = 5
MAX_PLANTED = 3


class DiskClient:
    """Stands in for ArenaClient over a harvested run (run manifest + blob store)."""

    def __init__(self, rdir: Path):
        self.rdir = rdir

    def run_manifest(self, run_id: str) -> dict:
        return json.loads((self.rdir / "manifest.json").read_text())

    def blob(self, run_id: str, sha: str, task_id: str | None = None) -> bytes:
        return (DATA / "blobs" / sha).read_bytes()


def dev_runs() -> list[Path]:
    runs = [r for r in (DATA / "runs").iterdir() if (r / "revealed.json").exists() and (r / "manifest.json").exists()]
    runs.sort(key=lambda p: (p / "run.json").stat().st_mtime if (p / "run.json").exists() else 0)
    return runs[:-HELD_OUT]


def build(rdir: Path) -> Index:
    return Index.build(DiskClient(rdir), rdir.name)


# --- 1. probes --------------------------------------------------------------------------------------------------
def check_probes(runs: list[Path], verbose: bool) -> bool:
    stats, bad = collections.Counter(), collections.defaultdict(list)
    for rdir in runs:
        recs = []
        if (pj := rdir / "probes.json").exists():
            pr = json.loads(pj.read_text())
            recs += [(pr["task_id"], p["tool"], p["args"], p["response"]) for p in pr["probes"] if "response" in p]
        for tdir in (rdir / "tasks").iterdir():
            tj = tdir / "tools.json"
            if tj.exists():
                for tool, resp in json.loads(tj.read_text()).items():
                    if tool in localtools.LOCAL_TOOLS and isinstance(resp, dict) and "ok" in resp:
                        recs.append((tdir.name, tool, {}, resp))
        recs = [r for r in recs if r[1] in localtools.LOCAL_TOOLS]
        if not recs:
            continue
        idx = build(rdir)
        for tid, tool, args, real in recs:
            mine = localtools.call(idx.view(tid), tool, dict(args))
            stats[tool] += 1
            if (d := first_diff(real, mine)):
                bad[tool].append((rdir.name, tid, args, d))
    for tool in sorted(stats):
        print(f"  {tool:<11} {stats[tool] - len(bad[tool]):>5}/{stats[tool]} identical")
        for run, tid, args, d in bad[tool][: (99 if verbose else 3)]:
            print(f"      {run} {tid} {json.dumps(args)[:80]}\n          {d}")
    return not any(bad.values())


# --- 2. citations -----------------------------------------------------------------------------------------------
def check_citations(runs: list[Path], verbose: bool) -> bool:
    ok = total = 0
    misses: list = []
    by_ext = collections.Counter()
    t0 = time.time()
    for rdir in runs:
        idx = build(rdir)
        for t in json.loads((rdir / "revealed.json").read_text())["tasks"]:
            view = idx.view(t["task_id"])
            for e in t.get("expected_sources") or []:
                total += 1
                u = view.find(e)
                if u is not None and any(_match(s, e) for s in u.sources()):
                    ok += 1
                else:
                    misses.append((rdir.name, t["task_id"], e))
                    by_ext[str(e.get("path", "")).rsplit(".", 1)[-1]] += 1
    share = ok / total if total else 1.0
    print(f"  {ok}/{total} expected citations resolve ({share:.2%}) over {len(runs)} runs in {time.time() - t0:.0f}s")
    if misses:
        print(f"  misses by type: {dict(by_ext)}")
        for m in misses[: (len(misses) if verbose else 15)]:
            print(f"      {m[0]} {m[1]} {json.dumps(m[2])}")
    return share >= 0.99


# --- 3. untrusted flags -----------------------------------------------------------------------------------------
def check_untrusted(runs: list[Path], verbose: bool) -> bool:
    """Planted text, labelled per carrier as the injection-landscape research found team attacks (each task's file
    variant against the run's base version = the most common sha per path):
      audit-detail   an audit event whose ts/agent/action/target/trace equal a base event but whose `detail` changed
      office-comment a comment (docx/xlsx/ods/odt) whose text the base version does not have
      pdf-page       a PDF page whose text the base page does not have (hidden text is drawn on an existing page)
    Inserted audit lines and other per-task differences are mostly the scenario's own per-task facts, so they are
    not counted. False positives = flagged units in base versions (scenario traps among them, so an upper bound)."""
    planted, caught, caught_spec = collections.Counter(), collections.Counter(), collections.Counter()
    base_units, base_flag = 0, collections.Counter()
    fp_ex, miss_ex = collections.Counter(), []
    for rdir in runs:
        idx = build(rdir)
        by_path = collections.defaultdict(collections.Counter)
        for files in idx.files.values():
            for p, h in files.items():
                by_path[p][h] += 1
        base = {p: c.most_common(1)[0][0] for p, c in by_path.items()}
        seen: set[str] = set()
        for tid, files in idx.files.items():
            view = idx.view(tid)
            for p, h in files.items():
                e = p.rsplit(".", 1)[-1].lower()
                if e in ("jpg", "png"):
                    continue
                if h == base[p]:
                    if h not in seen:
                        seen.add(h)
                        for u in view.units(p)[1:]:
                            base_units += 1
                            for w in u.why:
                                base_flag[w] += 1
                                fp_ex[(p, w)] += 1
                    continue
                if h in seen:
                    continue
                seen.add(h)
                for carrier, u in _planted(view, p, h, base[p]):
                    planted[carrier] += 1
                    if set(u.why) - {"variant-text"}:
                        caught_spec[carrier] += 1
                    if u.untrusted:
                        caught[carrier] += 1
                    elif len(miss_ex) < 200:
                        miss_ex.append((carrier, p, u.text[:200]))
    print("  recall on planted text (distinct variants); all signals | without variant-text:")
    for c in sorted(planted):
        print(f"      {c:<15} {caught[c]:>5}/{planted[c]:<5} {caught[c] / planted[c]:>4.0%} | "
              f"{caught_spec[c]:>5}/{planted[c]:<5} {caught_spec[c] / planted[c]:.0%}")
    n = sum(base_flag.values())
    print(f"  false positives: {n} flags on {base_units} base units ({n / max(1, base_units):.3%}): {dict(base_flag)}")
    for (p, w), k in fp_ex.most_common(60 if verbose else 10):
        print(f"      {k:>4}x {p} {w}")
    for m in miss_ex[: (200 if verbose else 10)]:
        print(f"      missed {m}")
    return True


def _planted(view, p: str, h: str, hb: str):
    e = p.rsplit(".", 1)[-1].lower()
    units, bunits = view.units(p), view.units_of_sha(p, hb)
    if p.startswith("/logs/audit/") and e == "jsonl":
        def key(t):
            try:
                ev = json.loads(t)
            except ValueError:
                return None, None
            if not isinstance(ev, dict):
                return None, None
            return tuple(str(ev.get(k)) for k in ("ts", "agent", "action", "target", "trace")), ev.get("detail")
        bmap = {}
        for u in bunits[1:]:
            k, d = key(u.text)
            if k:
                bmap[k] = d
        for u in units[1:]:
            k, d = key(u.text)
            if k in bmap and d != bmap[k]:
                yield "audit-detail", u
    elif e in ("docx", "odt", "xlsx", "ods"):
        btext = " ".join(u.text for u in bunits)
        for u in units[1:]:
            for c in re.findall(r"\[(?:cell )?comment: (.*?)\]$|^\[comment\] (.*)$", u.text):
                c = c[0] or c[1]
                if c and c not in btext:
                    yield "office-comment", u
                    break
    elif e == "pdf":
        btexts = {u.text for u in bunits}
        for u in units[1:]:
            if u.text not in btexts:
                yield "pdf-page", u


def main() -> int:
    args = sys.argv[1:]
    verbose = "-v" in args
    n = int(args[args.index("--runs") + 1]) if "--runs" in args else None
    what = next((a for a in args if a in ("probes", "citations", "untrusted", "all")), "all")
    runs = dev_runs()[:n] if n else dev_runs()
    ok = True
    for name, fn in (("probes", check_probes), ("citations", check_citations), ("untrusted", check_untrusted)):
        if what in (name, "all"):
            print(f"[{name}]")
            ok &= fn(runs, verbose)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
