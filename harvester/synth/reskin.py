"""Reskin harvested runs into another theme: same tasks and answers, every hub-theme word swapped.

    uv run --with pillow harvester/synth/reskin.py --theme port --last 5
    uv run harvester/replay/server.py --data harvester/data/synth/port --port 3997

Writes a data directory the replay server serves as is (`runs/<id>_<theme>/`, `blobs/`, `tools.json`) plus
`reports/<run>.json` (what changed, leftovers, image transcription quality).

What gets rewritten, consistently: file contents (text, JSON / JSONL string literals, docx / odt / xlsx / ods text
nodes, PDF text, images via transcript and redraw), file paths, task instructions, ticket requests, expected answers
and cited sources, the threat-intel feed and the recorded tool outputs. Line numbers never change, so cited lines stay
valid. Token fingerprints (`sha256_12` of the secret value) are recomputed for every secret value found in the run.

Images need one transcription each (the fixed model, cached in harvester/data/synth/transcripts.json, about $0.0002
an image); `--no-images` keeps the original images instead.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import zipfile
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from textmap import Reskin, load_theme  # noqa: E402

DATA = ROOT / "harvester" / "data"
TEXT = {"md", "txt", "csv", "html"}
JSONISH = {"json", "jsonl"}
ZIPS = {"docx", "odt", "xlsx", "ods"}
IMAGES = {"jpg", "jpeg", "png"}
SECRET = re.compile(r"fake_[a-z]{2,}_([0-9a-f]+)(?:[ \t]*\n[ \t]*([0-9a-f]{6,})\b)?")


def ext(p: str) -> str:
    return p.rsplit(".", 1)[-1].lower() if "." in p.rsplit("/", 1)[-1] else ""


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def deep(x, f):
    if isinstance(x, str):
        return f(x)
    if isinstance(x, list):
        return [deep(v, f) for v in x]
    if isinstance(x, dict):
        return {(f(k) if isinstance(k, str) else k): deep(v, f) for k, v in x.items()}
    return x


def rewrite_zip(data: bytes, r: Reskin) -> tuple[bytes | None, list[str]]:
    zin = zipfile.ZipFile(BytesIO(data))
    out = BytesIO()
    changed = False
    left: list[str] = []
    with zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            raw = zin.read(info.filename)
            if info.filename.endswith(".xml"):
                s = raw.decode("utf-8")
                new = r.xml_text(s)
                left += r.leftovers(re.sub(r"<[^>]+>", " ", new))
                if new != s:
                    raw = new.encode("utf-8")
                    changed = True
            zout.writestr(info, raw, compress_type=info.compress_type)
    return (out.getvalue() if changed else None), left


class Run:
    def __init__(self, src: Path):
        self.src = src
        self.id = src.name
        rd = lambda n: json.loads((src / n).read_text())
        self.run, self.manifest, self.revealed = rd("run.json"), rd("manifest.json"), rd("revealed.json")
        self.finish = rd("finish.json") if (src / "finish.json").exists() else None
        self.intel = rd("intel.json") if (src / "intel.json").exists() else None
        self.tasks = {t["task_id"]: json.loads((src / "tasks" / t["task_id"] / "task.json").read_text())
                      for t in self.run["tasks"]}
        self.tools = {tid: json.loads(p.read_text()) for tid in self.tasks
                      if (p := src / "tasks" / tid / "tools.json").exists()}
        self.files: dict[str, str] = {}  # sha -> a path it has
        for t in self.manifest["tasks"]:
            for p, s in t["files"].items():
                self.files.setdefault(s, p)
        self.blob_meta = {b["sha256"]: b for b in self.manifest.get("blobs", [])}
        self.revealed_by_id = {t["task_id"]: t for t in self.revealed["tasks"]}


def secret_values(texts: list[str]) -> set[str]:
    vals: set[str] = set()
    for t in texts:
        for m in SECRET.finditer(t):
            head = m.group(0).split("\n")[0].strip()
            vals.add(head)
            if m.group(2):
                vals.add(head + m.group(2))
    return vals


class Reskinner:
    def __init__(self, theme: dict, out: Path, images: bool, workers: int):
        self.theme, self.out, self.images, self.workers = theme, out, images, workers
        self.blobs = out / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        (out / "runs").mkdir(exist_ok=True)
        (out / "reports").mkdir(exist_ok=True)
        if (DATA / "tools.json").exists():
            shutil.copy(DATA / "tools.json", out / "tools.json")
        self.transcripts = None
        if images:
            from images import Transcripts
            self.transcripts = Transcripts(DATA / "synth" / "transcripts.json",
                                           [ROOT / "runs" / "vision_eval_cache.json"])

    def put_blob(self, data: bytes, old_sha: str | None = None) -> str:
        sha = sha256(data)
        dst = self.blobs / sha
        if not dst.exists():
            if old_sha and sha == old_sha:
                try:
                    os.link(DATA / "blobs" / old_sha, dst)
                    return sha
                except OSError:
                    pass
            dst.write_bytes(data)
        return sha

    # ----------------------------------------------------------------------------------------------------------
    def reskin_run(self, run: Run) -> dict:
        t0 = time.time()
        name = self.theme["name"]
        base = Reskin(self.theme)
        raw = {s: (DATA / "blobs" / s).read_bytes() for s in run.files}
        # secrets: recompute sha256_12 fingerprints for every value seen in plain or base64-decoded text
        texts = [raw[s].decode("utf-8", "replace") for s, p in run.files.items() if ext(p) in TEXT | JSONISH]
        decoded = []
        from textmap import _B64
        import base64
        for t in texts:
            for m in _B64.finditer(t):
                try:
                    decoded.append(base64.b64decode(m.group(0) + "=" * (-len(m.group(0)) % 4)).decode())
                except Exception:
                    pass
        extra = {}
        for v in secret_values(texts + decoded):
            nv = base.text(v)
            if nv != v:
                extra[sha256(v.encode())[:12]] = sha256(nv.encode())[:12]
        r = Reskin(self.theme, extra)
        rep = {"run": run.id, "theme": name, "blobs": {}, "leftovers": {}, "images": {}, "fingerprints": len(extra)}

        def count(k):
            rep["blobs"][k] = rep["blobs"].get(k, 0) + 1

        # open tickets: the known request text replaces the transcribed one
        requests: dict[str, set[str]] = {}
        for tid, task in run.tasks.items():
            req = (run.revealed_by_id.get(tid) or {}).get("request")
            for p, s in task["files"].items():
                if p.startswith("/tickets/") and ext(p) in IMAGES and req:
                    requests.setdefault(s, set()).add(req)

        if self.transcripts is not None:
            imgs = {s: (p, raw[s]) for s, p in run.files.items() if ext(p) in IMAGES}
            self.transcripts.fill(imgs, workers=self.workers, log=log)

        new_sha: dict[str, str] = {}
        for s, p in sorted(run.files.items(), key=lambda kv: kv[1]):
            e = ext(p)
            data = raw[s]
            new = None
            left: list[str] = []
            try:
                if e in TEXT or e in JSONISH:
                    txt = data.decode("utf-8")
                    out = r.json_text(txt) if e in JSONISH else r.text(txt)
                    left = r.leftovers(out)
                    new = out.encode("utf-8") if out != txt else None
                    count(f"{e}:{'changed' if new else 'same'}")
                elif e in ZIPS:
                    new, left = rewrite_zip(data, r)
                    count(f"{e}:{'changed' if new else 'same'}")
                elif e == "pdf":
                    from pdfedit import pdf_text, rewrite_pdf
                    if r.leftovers(pdf_text(data)):
                        new = rewrite_pdf(data, r.text)
                        left = r.leftovers(pdf_text(new if new else data))
                    count(f"pdf:{'changed' if new else 'same'}")
                elif e in IMAGES and self.transcripts is not None and s in self.transcripts.data:
                    new, info = self.redraw(s, p, data, r, requests.get(s))
                    if info:
                        rep["images"][p] = info
                    count(f"image:{'redrawn' if new else 'same'}")
                else:
                    count(f"{e or 'none'}:untouched")
            except Exception as ex:  # keep the original, report it
                rep["leftovers"][p] = [f"ERROR {type(ex).__name__}: {ex}"]
                count(f"{e}:error")
                new = None
            if left:
                rep["leftovers"][p] = sorted(set(left))[:20]
            new_sha[s] = self.put_blob(new if new is not None else data, None if new is not None else s)

        # Files that record another file's SHA-256 (the MCP approved-hash list: "exactly as served") must name the
        # rewritten file's hash. Rewritten manifests never hold hashes themselves, so one pass settles it.
        moved = {o: n for o, n in new_sha.items() if o != n}
        hex64 = re.compile(r"\b[0-9a-f]{64}\b")
        rep["hashes_remapped"] = 0
        for s, p in run.files.items():
            if ext(p) not in TEXT | JSONISH:
                continue
            body = (self.blobs / new_sha[s]).read_bytes().decode("utf-8")
            hits = [h for h in hex64.findall(body) if h in moved]
            if hits:
                rep["hashes_remapped"] += len(hits)
                new_sha[s] = self.put_blob(hex64.sub(lambda m: moved.get(m.group(0), m.group(0)), body).encode())

        self.write_run(run, r, new_sha)
        rep["seconds"] = round(time.time() - t0, 1)
        rep["leftover_files"] = len(rep["leftovers"])
        (self.out / "reports" / f"{run.id}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
        return rep

    def redraw(self, sha, path, data, r: Reskin, reqs: set[str] | None):
        from images import render, split_marks, with_request
        tr = self.transcripts.data[sha]
        info = {}
        lines, marks = split_marks(tr)
        is_open = bool(re.search(r"\bOPEN\b", (lines[0] if lines else "") + " " + marks))
        src = tr
        if reqs and len(reqs) == 1 and is_open:
            req = next(iter(reqs))
            m = re.search(r"Request\s*:\s*(.*)", "\n".join(lines), re.S | re.I)
            if m:
                norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()
                info["request_match"] = round(SequenceMatcher(None, norm(m.group(1)), norm(req)).ratio(), 3)
            src = with_request(tr, req)
        new_t = r.text(src)
        if new_t == tr:
            return None, info
        info["redrawn"] = True
        left = r.leftovers(new_t)
        if left:
            info["leftovers"] = left
        return render(new_t, "png" if path.lower().endswith(".png") else "jpg", seed=sha), info

    def write_run(self, run: Run, r: Reskin, new_sha: dict[str, str]) -> None:
        name = self.theme["name"]
        rid = f"{run.id}_{name}"
        dst = self.out / "runs" / rid
        if dst.exists():
            shutil.rmtree(dst)
        (dst / "tasks").mkdir(parents=True)
        tx = r.text
        paths = {}

        def np(p):
            if p not in paths:
                paths[p] = tx(p)
            return paths[p]

        def files(fm):
            out = {np(p): new_sha[s] for p, s in fm.items()}
            assert len(out) == len(fm), "two paths collapsed into one"
            return out

        blobs = []
        for old, new in sorted(set(new_sha.items()), key=lambda kv: kv[1]):
            meta = dict(run.blob_meta.get(old, {}))
            meta.update({"sha256": new, "size": (self.blobs / new).stat().st_size})
            blobs.append(meta)
        uniq = {b["sha256"]: b for b in blobs}
        manifest = {"run_id": rid, "blobs": list(uniq.values()),
                    "total_bytes": sum(b["size"] for b in uniq.values()),
                    "tasks": [{"task_id": t["task_id"], "files": files(t["files"])} for t in run.manifest["tasks"]]}
        runj = deep(run.run, tx)
        runj.update({"run_id": rid, "theme": name})
        rev = deep(run.revealed, tx)
        rev.update({"run_id": rid, "theme": name})
        write = lambda p, o: p.write_text(json.dumps(o, ensure_ascii=False))
        write(dst / "run.json", runj)
        write(dst / "manifest.json", manifest)
        write(dst / "revealed.json", rev)
        if run.finish:
            fin = deep(run.finish, tx)
            fin.update({"run_id": rid, "theme": name})
            write(dst / "finish.json", fin)
        if run.intel is not None:
            write(dst / "intel.json", deep(run.intel, tx))
        # for the replay server: the hub path of every renamed file, and the token-id prefix
        write(dst / "synth.json", {"theme": name, "source_run": run.id,
                                   "token_prefix": self.theme.get("token_prefix", "TOK"),
                                   "canon": {n: o for o, n in paths.items() if n != o}})
        for tid, task in run.tasks.items():
            (dst / "tasks" / tid).mkdir()
            t = deep({k: v for k, v in task.items() if k != "files"}, tx)
            t["files"] = files(task["files"])
            write(dst / "tasks" / tid / "task.json", t)
            if tid in run.tools:
                write(dst / "tasks" / tid / "tools.json", deep(run.tools[tid], tx))


def held_out(n: int) -> list[Path]:
    runs = [p for p in (DATA / "runs").iterdir()
            if (p / "run.json").exists() and (p / "revealed.json").exists() and (p / "manifest.json").exists()]
    runs.sort(key=lambda p: (p / "run.json").stat().st_mtime)  # the replay server's order
    return runs[-n:]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--theme", default="port", help="theme name (themes/<name>.json) or path")
    ap.add_argument("--last", type=int, default=0, metavar="N", help="the N most recent harvested runs (the held-out set)")
    ap.add_argument("--runs", nargs="*", default=[], help="harvested run ids")
    ap.add_argument("--out", type=Path, help="default harvester/data/synth/<theme>")
    ap.add_argument("--no-images", action="store_true", help="keep original images (no transcription calls)")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    theme = load_theme(a.theme)
    out = a.out or DATA / "synth" / theme["name"]
    srcs = [DATA / "runs" / r for r in a.runs] + (held_out(a.last) if a.last else [])
    if not srcs:
        ap.error("give --runs or --last")
    rs = Reskinner(theme, out, not a.no_images, a.workers)
    for i, src in enumerate(srcs, 1):
        run = Run(src)
        log(f"[{i}/{len(srcs)}] {run.id}: {len(run.files)} distinct files")
        rep = rs.reskin_run(run)
        imgs = rep["images"]
        matches = [v["request_match"] for v in imgs.values() if "request_match" in v]
        log(f"[{i}/{len(srcs)}] {run.id} done in {rep['seconds']}s: {rep['blobs']}, leftovers in "
            f"{rep['leftover_files']} files, {sum(1 for v in imgs.values() if v.get('redrawn'))} images redrawn, "
            f"open-ticket transcript match {min(matches, default=1):.2f}..{max(matches, default=1):.2f}, "
            f"fingerprints remapped {rep['fingerprints']}, file hashes remapped {rep['hashes_remapped']}")
    if rs.transcripts is not None:
        log(f"transcription: {rs.transcripts.calls} calls")
    log(f"data dir: {out}  (serve with: uv run harvester/replay/server.py --data {out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
