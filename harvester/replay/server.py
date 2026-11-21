"""Local replay of the Harness Arena Task API, serving harvested dev runs and grading submissions offline.

    uv run harvester/replay/server.py                      # :3998, every harvested run, in order
    uv run harvester/replay/server.py --exclude-runs 3     # hold the last 3 harvested runs out (keep a test set)
    uv run harvester/replay/server.py --only-runs 3        # serve only the last 3 (the test set)
    uv run harvester/replay/server.py --reveal             # include expected answers in submit feedback (debug)

Point the starter kit at it:
    ARENA_BASE_URL=http://localhost:3998 ARENA_API_KEY=arena_local uv run main.py --kind dev --limit 5

POST /runs {"kind":"dev"} hands out the next harvested run; {"seed": N} picks the harvested run with that seed.
Finished runs are written to harvester/replay_results/<run_id>.json.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import threading
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from replay import tools as T  # noqa: E402
from replay.grade import AUTHORISED, grade, task_kind  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
CONTENT_TYPES = {"pdf": "application/pdf", "jpg": "image/jpeg", "png": "image/png", "json": "application/json",
                 "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 "odt": "application/vnd.oasis.opendocument.text",
                 "ods": "application/vnd.oasis.opendocument.spreadsheet"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    """Harvested runs on disk."""

    def __init__(self, data: Path):
        self.data, self.blobs = data, data / "blobs"
        self.runs = {}
        for rdir in sorted((data / "runs").iterdir(), key=lambda p: (p / "run.json").stat().st_mtime
                           if (p / "run.json").exists() else 0):
            if (rdir / "revealed.json").exists() and (rdir / "manifest.json").exists():
                self.runs[rdir.name] = rdir
        tj = data / "tools.json"
        self.tools = json.loads(tj.read_text())["tools"] if tj.exists() else []
        self.page_counts: dict = {}

    def load(self, src: str) -> dict:
        rdir = self.runs[src]
        run = json.loads((rdir / "run.json").read_text())
        revealed = json.loads((rdir / "revealed.json").read_text())
        manifest = json.loads((rdir / "manifest.json").read_text())
        intel = json.loads((rdir / "intel.json").read_text()) if (rdir / "intel.json").exists() else {}
        synth = json.loads((rdir / "synth.json").read_text()) if (rdir / "synth.json").exists() else {}
        return {"run": run, "synth": synth, "revealed": {t["task_id"]: t for t in revealed["tasks"]}, "manifest": manifest,
                "intel": intel, "dir": rdir}


class Replay:
    def __init__(self, store: Store, order: list[str], reveal: bool, results: Path):
        self.store, self.order, self.reveal, self.results = store, order, reveal, results
        self.lock = threading.Lock()
        self.n_started = 0
        self.sessions: dict[str, dict] = {}
        self.next_idx = 0
        self.dyn_blobs: dict[str, bytes] = {}  # files written during a run, by sha256

    def start(self, body: dict) -> tuple[int, dict]:
        if body.get("kind", "dev") != "dev":
            return 403, {"error": "replay server: only dev runs exist here"}
        with self.lock:
            if body.get("seed") is not None:
                src = next((r for r in self.order if json.loads((self.store.runs[r] / "run.json").read_text())
                            .get("seed") == body["seed"]), None)
                if src is None:
                    return 404, {"error": f"no harvested run with seed {body['seed']}"}
            else:
                src = self.order[self.next_idx % len(self.order)]
                self.next_idx += 1
            self.n_started += 1  # counted under the lock: parallel starts must not share a run number
            n = self.n_started
        d = self.store.load(src)
        run_id = f"rp{n:03d}_{src}"
        tasks = {}
        files_by_task = {t["task_id"]: t["files"] for t in d["manifest"]["tasks"]}
        for t in d["run"]["tasks"]:
            tid = t["task_id"]
            tools_rec = d["dir"] / "tasks" / tid / "tools.json"
            rec_tree = None
            if tools_rec.exists():
                rec_tree = (json.loads(tools_rec.read_text()).get("tree") or {}).get("result")
            ws = T.Workspace(files_by_task[tid], self.store.blobs, self.store.page_counts, d["intel"], rec_tree,
                             canon=d["synth"].get("canon"), token_prefix=d["synth"].get("token_prefix", "TOK"))
            truth = d["revealed"][tid]
            ws.authorized = AUTHORISED.get(task_kind(truth.get("expected_outcome"), truth.get("expected_answer"),
                                                     truth.get("request") or t.get("instruction", "")), set())
            tasks[tid] = {"meta": t, "ws": ws, "tool_calls": 0, "submission": None, "grade": None,
                          "truth": d["revealed"][tid]}
        sess = {"run_id": run_id, "src": src, "run": d["run"], "tasks": tasks, "status": "active",
                "started_at": now(), "finished_at": None, "meta": body.get("meta"), "blob_types":
                {b["sha256"]: b for b in d["manifest"].get("blobs", [])}}
        with self.lock:
            self.sessions[run_id] = sess
        r = d["run"]
        print(f"[run] {run_id} <- {src} seed={r.get('seed')} ({len(tasks)} tasks)", flush=True)
        return 200, {"run_id": run_id, "kind": "dev", "theme": r.get("theme"), "seed": r.get("seed"),
                     "variation": r.get("variation"), "deadline_at": None, "replay_of": src,
                     "tasks": [t["meta"] for t in tasks.values()]}

    def summary(self, s: dict, with_tasks: bool) -> dict:
        grades = [t["grade"]["score"] if t["grade"] else 0.0 for t in s["tasks"].values()]
        fin = s["status"] == "finished"
        out = {"run_id": s["run_id"], "kind": "dev", "theme": s["run"].get("theme"), "seed": s["run"].get("seed"),
               "variation": s["run"].get("variation"), "status": s["status"], "meta": s["meta"],
               "started_at": s["started_at"], "deadline_at": None, "finished_at": s["finished_at"],
               "cost_usd": None, "accuracy": round(sum(grades) / len(grades), 4) if fin else None,
               "cost_score": None, "final_score": None, "replay_of": s["src"]}
        if with_tasks:
            out["tasks"] = []
            for tid, t in s["tasks"].items():
                row = {**t["meta"], "submitted": t["submission"] is not None, "tool_calls": t["tool_calls"],
                       "grade": t["grade"]}
                if fin:
                    tr = t["truth"]
                    row.update({k: tr.get(k) for k in ("expected_outcome", "expected_answer", "expected_sources",
                                                       "request")}, submission=t["submission"])
                out["tasks"].append(row)
        return out

    def finish(self, s: dict) -> dict:
        if s["status"] != "finished":
            s["status"], s["finished_at"] = "finished", now()
            self.results.mkdir(parents=True, exist_ok=True)
            full = self.summary(s, True)
            for row, t in zip(full["tasks"], s["tasks"].values()):
                row["actions"] = t["ws"].actions
            (self.results / f"{s['run_id']}.json").write_text(json.dumps(full, indent=2, ensure_ascii=False))
            sub = sum(t["submission"] is not None for t in s["tasks"].values())
            print(f"[finish] {s['run_id']} accuracy={full['accuracy']} ({sub}/{len(s['tasks'])} submitted)", flush=True)
        out = self.summary(s, False)
        out["tasks_submitted"] = sum(t["submission"] is not None for t in s["tasks"].values())
        out["tasks_total"] = len(s["tasks"])
        return out

    def manifest_entry(self, s: dict, ws: T.Workspace, path: str) -> dict:
        e = T.ext(path)
        if path in ws.overlay:
            data = ws.overlay[path]
            sha = hashlib.sha256(data).hexdigest()
            self.dyn_blobs[sha] = data
            size, ctype = len(data), "text/plain; charset=utf-8"
        else:
            sha = ws.base[path]
            b = s["blob_types"].get(sha, {})
            size, ctype = b.get("size") or ws.size(path), b.get("content_type") or CONTENT_TYPES.get(e)
        return {"path": path, "sha256": sha, "size": size, "kind": T.FORMAT.get(e, e) if e in T.TEXT_EXT else e,
                "content_type": ctype or "application/octet-stream", "readable": e in T.TEXT_EXT}


def make_handler(rp: Replay):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _json(self, code: int, obj) -> None:
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _bytes(self, data: bytes, ctype: str) -> None:
            self.send_response(200)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw or b"{}")
            except ValueError:
                return None

        def _auth(self) -> bool:
            if self.headers.get("authorization", "").startswith("Bearer arena_"):
                return True
            self._json(401, {"error": "missing or invalid API key (any 'Bearer arena_...' works locally)"})
            return False

        def _session(self, run_id: str, need_active: bool):
            s = rp.sessions.get(run_id)
            if s is None:
                self._json(404, {"error": f"unknown run {run_id}"})
                return None
            if need_active and s["status"] != "active":
                self._json(409, {"error": "run is finished"})
                return None
            return s

        def _task(self, s, tid):
            t = s["tasks"].get(tid)
            if t is None:
                self._json(404, {"error": f"unknown task {tid}"})
            return t

        def do_GET(self):
            if not self._auth():
                return
            u = urlparse(self.path)
            p, q = unquote(u.path), parse_qs(u.query)
            if p == "/api/v1/me":
                return self._json(200, {"team": {"id": "local", "name": "local replay"},
                                        "final_window": {"open": False, "opens_at": None, "closes_at": None},
                                        "final_run_used": False})
            if p == "/api/v1/tools":
                return self._json(200, {"tools": rp.store.tools})
            if p == "/api/v1/runs":
                return self._json(200, {"runs": [rp.summary(s, False) for s in rp.sessions.values()]})
            m = re.fullmatch(r"/api/v1/runs/([^/]+)(/.*)?", p)
            if not m:
                return self._json(404, {"error": "not found"})
            run_id, rest = m.group(1), m.group(2) or ""
            if rest == "":
                s = self._session(run_id, False)
                return s and self._json(200, rp.summary(s, True))
            s = self._session(run_id, False)  # downloads stay available read-only after a dev run ends
            if not s:
                return
            if rest == "/manifest":
                bl = list(s["blob_types"].values())
                return self._json(200, {"run_id": run_id, "blobs": bl, "total_bytes": sum(b.get("size", 0) for b in bl),
                                        "tasks": [{"task_id": tid, "files": dict(t["ws"].base)}
                                                  for tid, t in s["tasks"].items()]})
            mb = re.fullmatch(r"/blobs/([0-9a-f]{64})", rest)
            if mb:
                sha = mb.group(1)
                if sha in rp.dyn_blobs:
                    return self._bytes(rp.dyn_blobs[sha], "application/octet-stream")
                if sha in s["blob_types"] and (rp.store.blobs / sha).exists():
                    return self._bytes((rp.store.blobs / sha).read_bytes(),
                                       s["blob_types"][sha].get("content_type", "application/octet-stream"))
                return self._json(404, {"error": "blob not part of this run"})
            mt = re.fullmatch(r"/tasks/([^/]+)(/.*)", rest)
            if not mt:
                return self._json(404, {"error": "not found"})
            t = self._task(s, mt.group(1))
            if not t:
                return
            ws, sub = t["ws"], mt.group(2)
            if sub == "/manifest":
                return self._json(200, {"run_id": run_id, "task_id": mt.group(1),
                                        "files": [rp.manifest_entry(s, ws, f) for f in ws.paths()]})
            if sub.startswith("/files/"):
                fp = "/" + sub[len("/files/"):].lstrip("/")
                if fp not in ws.base and fp not in ws.overlay:
                    return self._json(404, {"error": f"file not found: {fp}"})
                return self._bytes(ws.data(fp), rp.manifest_entry(s, ws, fp)["content_type"])
            if sub == "/workspace.zip":
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    for fp in ws.paths():
                        z.writestr(fp.lstrip("/"), ws.data(fp))
                return self._bytes(buf.getvalue(), "application/zip")
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._auth():
                return
            body = self._body()
            if body is None:
                return self._json(400, {"error": "body is not valid JSON"})
            p = unquote(urlparse(self.path).path)
            if p == "/api/v1/runs":
                return self._json(*rp.start(body))
            m = re.fullmatch(r"/api/v1/runs/([^/]+)/finish", p)
            if m:
                s = self._session(m.group(1), False)
                return s and self._json(200, rp.finish(s))
            m = re.fullmatch(r"/api/v1/runs/([^/]+)/tasks/([^/]+)/(tools/([^/]+)|submit)", p)
            if not m:
                return self._json(404, {"error": "not found"})
            s = self._session(m.group(1), True)
            if not s:
                return
            t = self._task(s, m.group(2))
            if not t:
                return
            with rp.lock:
                if m.group(4):
                    if t["submission"] is not None:  # docs: once submitted, the workspace is frozen
                        return self._json(409, {"error": "task already submitted: its workspace is frozen"})
                    t["tool_calls"] += 1
                    return self._json(200, T.call(t["ws"], m.group(4), body if isinstance(body, dict) else {}))
                if t["submission"] is not None:
                    return self._json(409, {"error": "task already submitted"})
                if body.get("outcome") not in ("OK", "DENIED_SECURITY", "NEEDS_CLARIFICATION", "UNSUPPORTED"):
                    return self._json(400, {"error": "outcome must be OK, DENIED_SECURITY, NEEDS_CLARIFICATION "
                                                     "or UNSUPPORTED"})
                if not isinstance(body.get("sources", []), list):
                    return self._json(400, {"error": "sources must be an array"})
                truth = {**t["truth"], "_tool_calls": t["tool_calls"]}
                g = grade(truth, body, t["ws"], reveal=rp.reveal)
                t["submission"], t["grade"] = body, g
            print(f"[submit] {s['run_id']} {m.group(2)} L{t['meta'].get('level')} {g['replay_kind']:<24} "
                  f"score={g['score']:.2f}  {' | '.join(g['feedback'][:4])}", flush=True)
            g = {k: v for k, v in g.items() if k != "replay_kind"}
            return self._json(200, g)

    return H


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--port", type=int, default=3998)
    ap.add_argument("--data", type=Path, default=HERE / "data")
    ap.add_argument("--results", type=Path, default=HERE / "replay_results")
    ap.add_argument("--exclude-runs", type=int, default=0, metavar="N", help="hold out the N most recent harvested runs")
    ap.add_argument("--only-runs", type=int, default=0, metavar="N", help="serve only the N most recent harvested runs")
    ap.add_argument("--reveal", action="store_true", help="put expected answers in submit feedback (debugging)")
    a = ap.parse_args()
    store = Store(a.data)
    order = list(store.runs)
    if a.exclude_runs:
        order = order[:-a.exclude_runs]
    if a.only_runs:
        order = order[-a.only_runs:]
    if not order:
        sys.exit("no harvested runs to serve (need runs/<id>/{run,manifest,revealed}.json)")
    rp = Replay(store, order, a.reveal, a.results)
    n_tasks = sum(len(json.loads((store.runs[r] / "run.json").read_text())["tasks"]) for r in order)
    print(f"replay Task API on http://localhost:{a.port}  ({len(order)} runs, {n_tasks} tasks)"
          f"{'  [reveal on]' if a.reveal else ''}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(rp)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
