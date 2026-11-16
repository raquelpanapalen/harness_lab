"""Harvest Harness Arena dev runs into a local dataset: tasks, workspaces, and the revealed answers.

No model calls, no OpenRouter spend, no submissions. Per run:
  1. POST /runs {"kind": "dev"}               -> tasks (id, level, instruction), seed, theme, variation
  2. run manifest + every distinct blob          -> workspaces, stored once by sha256
     (fallback: per-task workspace.zip if the platform has no manifests)
  3. optional read-only tool recordings          -> e.g. `tree` output per task, for a replay server
  4. POST /runs/{id}/finish                      -> closes the run (downloads stop working after this)
  5. GET  /runs/{id}                             -> expected answers, revealed once a dev run has ended

Usage (from the repo root, needs ARENA_API_KEY + ARENA_BASE_URL in .env):
    uv run harvester/harvest.py --check                 # just call /me and /tools
    uv run harvester/harvest.py --runs 1                # harvest one fresh dev run
    uv run harvester/harvest.py --runs 20 --sleep 5     # build a dataset
    uv run harvester/harvest.py --seed 123456           # replay a seed (same tasks, new surface)
    uv run harvester/harvest.py --resume <run_id>       # finish a harvest that stopped half-way
    uv run harvester/harvest.py --runs 1 --materialize  # also write each task's files as a normal folder tree
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import logging

logging.getLogger("pypdf").setLevel(logging.ERROR)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # import the starter kit's client
from arena.client import READ_ONLY_TOOLS, ArenaClient, ArenaError, BlobCache  # noqa: E402

HERE = Path(__file__).resolve().parent


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


def safe_rel(path: str) -> PurePosixPath | None:
    """Workspace path -> safe relative path, or None if it tries to escape (../, absolute drive, etc.)."""
    p = PurePosixPath("/" + path.lstrip("/"))
    parts = [x for x in p.parts[1:] if x not in ("", ".")]
    if not parts or any(x == ".." or "\\" in x or ":" in x for x in parts):
        return None
    return PurePosixPath(*parts)


INDICATOR_RX = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|\b(?:\d{1,3}\.){3}\d{1,3}\b|"
                          r"\b(?:[a-z0-9-]+\.)+(?:example|test|invalid|com|net|org|io|ai|eu|dev|app|xyz|info|co|ru|cn)\b",
                          re.I)


def indicator_rank(x: str) -> tuple:
    """Domains first (what tasks ask about), then e-mail addresses, then IPs (mostly hosting-range noise)."""
    return (2 if re.fullmatch(r"[\d.]+", x) else 1 if "@" in x else 0, x)


def blob_text(path: str, data: bytes | None) -> str:
    """Searchable text of a workspace file: plain text as is, Office files from their XML, PDFs via pypdf.
    Images are skipped (no OCR)."""
    if not data:
        return ""
    e = path.rsplit(".", 1)[-1].lower()
    if e in ("md", "txt", "csv", "json", "jsonl"):
        return data.decode("utf-8", "replace")
    try:
        if e in ("docx", "xlsx", "odt", "ods"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = " ".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(".xml"))
            return re.sub(r"<[^>]+>", " ", xml)
        if e == "pdf" and len(data) < 5_000_000:
            from pypdf import PdfReader
            return " ".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages[:50])
    except Exception:
        return ""
    return ""


class Harvester:
    def __init__(self, out: Path, workers: int, record: list[str]):
        self.out = out
        self.cache = BlobCache(out / "blobs")
        self.client = ArenaClient(cache=self.cache)
        self.workers = workers
        bad = [t for t in record if t not in READ_ONLY_TOOLS]
        if bad:  # never call anything that changes task state: it would change what the grader expects
            raise SystemExit(f"refusing to record non-read-only tools: {bad}")
        self.record = record

    # --- one-off ---------------------------------------------------------------------------------
    def check(self) -> dict:
        me = self.client.me()
        tools = [t.model_dump() for t in self.client.tools()]
        dump(self.out / "tools.json", {"fetched_at": now(), "tools": tools})
        return {"me": me, "tools": [t["name"] for t in tools]}

    # --- per run ---------------------------------------------------------------------------------
    def start(self, seed: int | None) -> tuple[str, dict]:
        run = self.client.start_run("dev", seed=seed, meta={"note": "harvester: dataset collection, no submissions"})
        raw = run.model_dump()
        rdir = self.out / "runs" / run.run_id
        dump(rdir / "run.json", {"started_at": now(), **raw})
        return run.run_id, raw

    def fetch_files(self, run_id: str, tasks: list[dict]) -> dict[str, dict[str, str]]:
        """Return {task_id: {path: sha256}}; all blobs end up in out/blobs/."""
        rdir = self.out / "runs" / run_id
        try:
            m = self.client.run_manifest(run_id)
        except ArenaError as e:
            if e.status != 404:
                raise
            return self._fetch_zips(run_id, tasks)
        dump(rdir / "manifest.json", m)
        files = {t["task_id"]: t["files"] for t in m.get("tasks", [])}
        owner: dict[str, str] = {}
        for tid, fs in files.items():
            for h in fs.values():
                owner.setdefault(h, tid)
        missing = [h for h in owner if self.cache.get(h) is None]
        print(f"  {len(owner)} distinct files, {len(missing)} to download")
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            list(ex.map(lambda h: self.client.blob(run_id, h, owner[h]), missing))
        return files

    def _fetch_zips(self, run_id: str, tasks: list[dict]) -> dict[str, dict[str, str]]:
        print("  no run manifest: falling back to workspace.zip per task")

        def one(tid: str) -> tuple[str, dict[str, str]]:
            data = self.client.download_workspace(run_id, tid)
            fs: dict[str, str] = {}
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    if info.is_dir() or safe_rel(info.filename) is None:
                        continue
                    b = z.read(info)
                    h = hashlib.sha256(b).hexdigest()
                    if self.cache.get(h) is None:
                        self.cache.put(h, b)
                    fs["/" + str(safe_rel(info.filename))] = h
            return tid, fs

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            return dict(ex.map(one, [t["task_id"] for t in tasks]))

    def record_tools(self, run_id: str, tasks: list[dict]) -> None:
        if not self.record:
            return

        def one(tid: str) -> None:
            rec = {}
            for tool in self.record:
                try:
                    rec[tool] = self.client.call_tool(run_id, tid, tool, {})
                except ArenaError as e:
                    rec[tool] = {"ok": False, "error": f"harvester: {e}"}
            dump(self.out / "runs" / run_id / "tasks" / tid / "tools.json", rec)

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            list(ex.map(one, [t["task_id"] for t in tasks]))

    def record_intel(self, run_id: str, tasks: list[dict], files: dict[str, dict[str, str]], limit: int) -> None:
        """Look up every domain / e-mail / IP that appears in the run's text files once, so the replay server can
        answer `threat_intel` (the only tool whose data lives on the platform, not in the workspace)."""
        if not tasks or limit <= 0:
            return
        tid = tasks[0]["task_id"]
        found: set[str] = set()
        rx = INDICATOR_RX
        seen_blobs: set[str] = set()
        for fs in files.values():
            for path, h in fs.items():
                if h in seen_blobs:
                    continue
                seen_blobs.add(h)
                text = blob_text(path, self.cache.get(h))
                found.update(m.group(0).strip(".").lower() for m in rx.finditer(text))
        # Domains first (what tasks ask about), then e-mail addresses, then IPs (mostly hosting-range noise).
        todo = sorted(found, key=indicator_rank)[:limit]
        print(f"  threat_intel: {len(found)} indicators found, recording {len(todo)}")

        def one(ind: str):
            try:
                return ind, self.client.call_tool(run_id, tid, "threat_intel", {"indicator": ind})
            except ArenaError as e:
                return ind, {"ok": False, "error": f"harvester: {e}"}

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            dump(self.out / "runs" / run_id / "intel.json", dict(ex.map(one, todo)))

    def probe_tools(self, run_id: str, tasks: list[dict], files: dict[str, dict[str, str]]) -> None:
        """Record real responses of the read-only tools for a spread of arguments, including error cases, so the
        replay server can match the platform's exact output formats. Never calls a state-changing tool."""
        if not tasks:
            return
        tid = tasks[0]["task_id"]
        fs = files.get(tid, {})
        txt = lambda p: (self.cache.get(fs[p]) or b"").decode("utf-8", "replace") if p in fs else ""
        reg_p = next((p for p in fs if p.startswith("/fleet/") and "registry" in p and p.endswith(".json")), None)
        audit_p = next((p for p in sorted(fs) if p.startswith("/logs/audit/") and p.endswith(".jsonl")), None)
        pdf_p = next((p for p in sorted(fs) if p.endswith(".pdf")), None)
        agents, scope = [], "read:billing"
        try:
            reg = json.loads(txt(reg_p)) if reg_p else {}
            agents = [a["id"] for a in reg.get("agents", [])]
            scope = next(iter(reg.get("scope_classes", {})), scope)
        except (ValueError, KeyError, TypeError):
            pass
        a1 = agents[0] if agents else "nobody-01"
        a2 = agents[1] if len(agents) > 1 else a1
        ev = {}
        if audit_p:
            for line in txt(audit_p).splitlines()[100:400]:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("trace"):
                    ev = e
                    break
        probes = [
            ("tree", {"depth": 1}), ("tree", {"path": "/fleet", "depth": 2}), ("tree", {"path": "/nope"}),
            ("list", {}), ("list", {"path": "/fleet"}), ("list", {"path": "/logs"}), ("list", {"path": "/nope"}),
            ("read", {"path": "/AGENTS.md"}), ("read", {"path": "/AGENTS.md", "start_line": 2, "end_line": 4}),
            ("read", {"path": "/nope.md"}), ("read", {"path": "/fleet"}),
            ("search", {"pattern": a1}), ("search", {"pattern": "the", "limit": 3}),
            ("search", {"pattern": r"TOK-[A-Z]+-\d+", "regex": True, "limit": 3}),
            ("search", {"pattern": "[", "regex": True}), ("search", {"pattern": a1, "path": "/fleet"}),
            ("search", {"pattern": "zzzz-no-such-text-zzzz"}),
            ("query_logs", {"limit": 3}), ("query_logs", {"agent": a1, "limit": 3}),
            ("query_logs", {"action": "http_post", "limit": 3}), ("query_logs", {"text": "declined", "limit": 3}),
            ("query_logs", {"since": "2026-11-14T03:00:00Z", "until": "2026-11-14T03:10:00Z", "limit": 3}),
            ("query_logs", {"until": "2026-11-14T00:00:00Z", "limit": 3}),
            ("query_logs", {"limit": 2, "offset": 2}), ("query_logs", {"since": "yesterday"}),
            ("query_logs", {"target": "customer", "limit": 3}),
            ("access_path", {"from": a1, "to": "outbound"}), ("access_path", {"from": a1, "to": "private_data"}),
            ("access_path", {"from": a2, "to": scope}), ("access_path", {"from": a1, "to": "untrusted_input"}),
            ("access_path", {"from": a1, "to": "outbound", "exclude": [a2]}),
            ("access_path", {"from": a1, "to": "outbound", "added": [[a1, a2]], "revoked": [[a2, scope]]}),
            ("access_path", {"from": "nobody-00", "to": "outbound"}), ("access_path", {"from": a1, "to": "nonsense:scope"}),
            ("threat_intel", {"indicator": "surely-unknown-indicator.example"}), ("threat_intel", {}),
            ("get_containment", {}), ("no_such_tool", {}),
        ]
        if ev:
            probes.append(("query_logs", {"trace": ev["trace"]}))
        if audit_p:
            probes += [("read", {"path": audit_p}), ("read", {"path": audit_p, "start_line": 399, "end_line": 402})]
        if pdf_p:
            probes.append(("read", {"path": pdf_p}))

        def one(pr):
            tool, args = pr
            try:
                return {"tool": tool, "args": args, "response": self.client.call_tool(run_id, tid, tool, args)}
            except ArenaError as e:
                return {"tool": tool, "args": args, "http_error": {"status": e.status, "message": str(e)}}

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            out = list(ex.map(one, probes))
        dump(self.out / "runs" / run_id / "probes.json", {"task_id": tid, "probes": out})
        print(f"  probes: {len(out)} read-only tool responses recorded")

    def backfill_intel(self, src: str, limit: int) -> None:
        """Fill in threat_intel for a finished harvested run: replay its seed (same scenario and answers), look up
        every indicator from its files AND from its revealed answers/requests (some only appear in scanned images),
        then finish the replay run. Verdicts already recorded are spot-checked for consistency."""
        rdir = self.out / "runs" / src
        run = json.loads((rdir / "run.json").read_text())
        revealed = json.loads((rdir / "revealed.json").read_text())
        manifest = json.loads((rdir / "manifest.json").read_text())
        ipath = rdir / "intel.json"
        intel = json.loads(ipath.read_text()) if ipath.exists() else {}
        found: set[str] = set()
        seen: set[str] = set()
        for t in manifest["tasks"]:
            for path, h in t["files"].items():
                if h not in seen:
                    seen.add(h)
                    found.update(m.group(0).strip(".").lower() for m in INDICATOR_RX.finditer(blob_text(path, self.cache.get(h))))
        for t in revealed["tasks"]:
            blob = json.dumps(t.get("expected_answer"), ensure_ascii=False) + " " + (t.get("request") or "")
            found.update(m.group(0).strip(".").lower() for m in INDICATOR_RX.finditer(blob))
        todo = sorted(found - set(intel), key=indicator_rank)[:limit]
        spot = sorted(set(intel) & found)[:15]
        if not todo:
            print(f"  {src}: nothing missing ({len(intel)} recorded)")
            return
        rp = self.client.start_run("dev", seed=run["seed"], meta={"note": f"harvester: threat-intel backfill for {src}"})
        tid = rp.tasks[0].task_id
        print(f"  {src}: seed {run['seed']} -> {rp.run_id}; looking up {len(todo)} missing, spot-checking {len(spot)}")

        def one(ind: str):
            try:
                return ind, self.client.call_tool(rp.run_id, tid, "threat_intel", {"indicator": ind})
            except ArenaError as e:
                return ind, {"ok": False, "error": f"harvester: {e}"}

        try:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                got = dict(ex.map(one, todo + spot))
        finally:
            self.client.finish(rp.run_id)
        diff = [i for i in spot if got[i].get("result") != intel[i].get("result")]
        if diff:
            print(f"  WARNING: {len(diff)}/{len(spot)} spot-checked verdicts differ on the replayed seed, e.g. {diff[:3]}")
        intel.update({i: got[i] for i in todo})
        dump(ipath, intel)
        print(f"  {src}: intel.json now has {len(intel)} indicators")

    def save_tasks(self, run_id: str, tasks: list[dict], files: dict[str, dict[str, str]], materialize: bool) -> None:
        for t in tasks:
            tid = t["task_id"]
            tdir = self.out / "runs" / run_id / "tasks" / tid
            dump(tdir / "task.json", {**t, "files": files.get(tid, {})})
            if materialize:
                for path, h in files.get(tid, {}).items():
                    rel = safe_rel(path)
                    if rel is None:
                        print(f"  skipped unsafe path {path!r}")
                        continue
                    dst = tdir / "workspace" / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if not dst.exists():
                        try:
                            os.link(self.cache.path(h), dst)  # hardlink: no extra disk space
                        except OSError:
                            dst.write_bytes(self.cache.path(h).read_bytes())

    def close(self, run_id: str) -> dict:
        rdir = self.out / "runs" / run_id
        fin = self.client.finish(run_id)
        dump(rdir / "finish.json", {"finished_at": now(), **(fin if isinstance(fin, dict) else {"raw": fin})})
        revealed = self.client.get_run(run_id)
        dump(rdir / "revealed.json", revealed)
        return revealed

    def harvest(self, seed: int | None, resume: str | None, materialize: bool, intel: int = 0,
                probe: bool = True) -> None:
        if resume:
            run_id = resume
            raw = json.loads((self.out / "runs" / run_id / "run.json").read_text())
            print(f"resuming {run_id}")
        else:
            run_id, raw = self.start(seed)
        tasks = raw.get("tasks", [])
        print(f"run {run_id}: theme={raw.get('theme')} seed={raw.get('seed')} variation={raw.get('variation')} "
              f"tasks={len(tasks)}")
        try:
            files = self.fetch_files(run_id, tasks)
            self.save_tasks(run_id, tasks, files, materialize)
            self.record_tools(run_id, tasks)
            self.record_intel(run_id, tasks, files, intel)
            if probe:
                self.probe_tools(run_id, tasks, files)
        except BaseException:
            # Do NOT finish: once finished, downloads stop. Leave it open so --resume can complete it.
            print(f"\n  harvest of {run_id} stopped before finishing. Continue with: --resume {run_id}", file=sys.stderr)
            raise
        self.close(run_id)
        with (self.out / "index.jsonl").open("a") as f:
            f.write(json.dumps({"run_id": run_id, "seed": raw.get("seed"), "theme": raw.get("theme"),
                                "variation": raw.get("variation"), "tasks": len(tasks),
                                "replay_of": seed, "harvested_at": now()}) + "\n")
        print(f"  done: {self.out / 'runs' / run_id}  ({self.client.stats})")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--out", type=Path, default=HERE / "data", help="dataset folder (default %(default)s)")
    p.add_argument("--check", action="store_true", help="only call /me and /tools, save tools.json")
    p.add_argument("--runs", type=int, default=1, help="number of fresh dev runs to harvest")
    p.add_argument("--seed", type=int, help="replay this seed instead of a fresh scenario (one run)")
    p.add_argument("--resume", metavar="RUN_ID", help="finish a harvest that stopped before /finish")
    p.add_argument("--record", default="tree,get_containment", help="comma-separated read-only no-arg tools to record per task "
                                                     "('' for none; default %(default)s)")
    p.add_argument("--record-intel", type=int, default=2000, metavar="N",
                   help="record threat_intel for up to N indicators found in the run (0 = off; default %(default)s)")
    p.add_argument("--backfill-intel", nargs="*", metavar="RUN_ID",
                   help="fill in threat_intel for finished harvested runs via a seed replay (no ids = all runs)")
    p.add_argument("--no-probe", action="store_true", help="skip recording sample read-only tool responses")
    p.add_argument("--materialize", action="store_true", help="also write task files as folders (hardlinked)")
    p.add_argument("--workers", type=int, default=4, help="parallel downloads (API limit ~20 req/s)")
    p.add_argument("--sleep", type=float, default=2.0, help="seconds between runs")
    a = p.parse_args()

    h = Harvester(a.out, a.workers, [t for t in a.record.split(",") if t])
    info = h.check()
    print(f"team: {info['me'].get('team')}  tools: {', '.join(info['tools'])}")
    if a.check:
        return 0
    if a.backfill_intel is not None:
        ids = a.backfill_intel or sorted(p.name for p in (a.out / "runs").iterdir() if (p / "revealed.json").exists())
        for i, rid in enumerate(ids):
            if i:
                time.sleep(a.sleep)
            h.backfill_intel(rid, a.record_intel)
        return 0
    if a.resume:
        h.harvest(None, a.resume, a.materialize, a.record_intel, not a.no_probe)
        return 0
    n = 1 if a.seed is not None else a.runs
    for i in range(n):
        if i:
            time.sleep(a.sleep)
        print(f"[{i + 1}/{n}]", end=" ")
        h.harvest(a.seed, None, a.materialize, a.record_intel, not a.no_probe)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ArenaError as e:
        sys.exit(f"error: {e}")
    except KeyboardInterrupt:
        sys.exit(130)
