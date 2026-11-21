"""Check a reskinned data dir against its source runs (no model calls).

    uv run harvester/synth/check.py port

Per run: every text file keeps its line count, every expected source path exists in its task and every cited record
id is still in the cited file, token fingerprints match as many secret values per task as in the source, and no
hub-theme string is left outside the files the build report already lists.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[1] / "harvester" / "data"
sys.path.insert(0, str(HERE))
from textmap import _B64, Reskin, load_theme  # noqa: E402

TEXT = (".md", ".txt", ".csv", ".json", ".jsonl", ".html")
SECRET = re.compile(r"fake_[a-z]{2,}_([0-9a-f]+)(?:[ \t]*\n[ \t]*([0-9a-f]{6,})\b)?")


def fingerprint_hits(blobs: Path, files: dict[str, str]) -> int:
    t = "\n".join((blobs / s).read_text(errors="ignore") for p, s in files.items() if p.endswith(TEXT))
    for m in _B64.finditer(t):
        try:
            t += "\n" + base64.b64decode(m.group(0) + "=" * (-len(m.group(0)) % 4)).decode()
        except Exception:
            pass
    vals = set()
    for m in SECRET.finditer(t):
        head = m.group(0).split("\n")[0].strip()
        vals |= {head, head + (m.group(2) or "")}
    shas = {hashlib.sha256(v.encode()).hexdigest()[:12] for v in vals}
    n = 0
    for p, s in files.items():
        if "fingerprint" in p and p.endswith(".csv"):
            n += sum(any(c in shas for c in row) for row in list(csv.reader(io.StringIO((blobs / s).read_text())))[1:] if row)
    return n


def main() -> int:
    theme = load_theme(sys.argv[1] if len(sys.argv) > 1 else "port")
    out = DATA / "synth" / theme["name"]
    r = Reskin(theme)
    problems = 0
    for rdir in sorted((out / "runs").iterdir()):
        src = DATA / "runs" / rdir.name.rsplit(f"_{theme['name']}", 1)[0]
        old = json.loads((src / "manifest.json").read_text())
        new = json.loads((rdir / "manifest.json").read_text())
        rev = json.loads((rdir / "revealed.json").read_text())
        report = json.loads((out / "reports" / f"{src.name}.json").read_text())
        known = set(report["leftovers"])
        lines = sources = fps = left = 0
        newfiles = {t["task_id"]: t["files"] for t in new["tasks"]}
        for to in old["tasks"]:
            tn = newfiles[to["task_id"]]
            for p, s in to["files"].items():
                pn = r.text(p)
                if pn not in tn:
                    print(f"  {rdir.name} {to['task_id']}: path lost {p}")
                    problems += 1
                    continue
                if p.endswith(TEXT):
                    a = (DATA / "blobs" / s).read_bytes()
                    b = (out / "blobs" / tn[pn]).read_bytes()
                    if a.count(b"\n") != b.count(b"\n"):
                        lines += 1
                    if p not in known and r.leftovers(b.decode("utf-8", "replace")):
                        left += 1
            by_old = {p: tn[r.text(p)] for p in to["files"] if r.text(p) in tn}  # files under their hub names
            if fingerprint_hits(DATA / "blobs", to["files"]) != fingerprint_hits(out / "blobs", by_old):
                fps += 1
        for t in rev["tasks"]:
            f = newfiles[t["task_id"]]
            for c in t.get("expected_sources") or []:
                p = c.get("path")
                if p not in f:
                    sources += 1
                elif "record" in c and p.endswith(TEXT) and str(c["record"]) not in (out / "blobs" / f[p]).read_text(errors="ignore"):
                    sources += 1
            if r.leftovers(json.dumps(t.get("expected_answer"), ensure_ascii=False)) or r.leftovers(t.get("request") or ""):
                left += 1
        bad = lines + sources + fps + left
        problems += bad
        print(f"{rdir.name}: line-count changes {lines}, broken sources {sources}, fingerprint mismatches {fps}, "
              f"unexpected leftovers {left}, known leftover files {len(known)} -> {'OK' if not bad else 'FAIL'}")
    print("ALL OK" if not problems else f"{problems} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
