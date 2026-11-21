"""Offline: every relative Markdown link in the tracked docs points at a file or directory that exists (anchors and
external URLs are skipped). python tests/check_links.py"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
files = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True).stdout.split()
files += [str(p.relative_to(ROOT)) for p in ROOT.glob("docs/**/*.md")]  # new docs before they are committed
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
bad = []
for f in sorted(set(files)):
    path = ROOT / f
    if not path.exists():
        continue
    text = re.sub(r"```.*?```", "", path.read_text(), flags=re.S)  # code blocks are not links
    for target in LINK.findall(text):
        if re.match(r"[a-z]+:", target) or target.startswith("#"):
            continue
        dest = (path.parent / target.split("#", 1)[0]).resolve()
        if not dest.exists():
            bad.append(f"{f}: {target}")
print("\n".join(bad) or f"links ok in {len(set(files))} markdown files")
sys.exit(1 if bad else 0)
