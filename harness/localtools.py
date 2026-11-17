"""The platform's read-only tools (`tree`, `list`, `read`, `search`, `query_logs`) answered from the workspace index,
with the server's exact output (checked against every recorded server response: tests/check_index.py).

    call(view, "read", {"path": "/AGENTS.md"})  ->  {"ok": True, "result": {...}}  |  {"ok": False, "error": "..."}
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .index import TEXT_EXT, TaskView, ext

LOCAL_TOOLS = {"tree", "list", "read", "search", "query_logs"}
FORMAT = {"md": "markdown", "txt": "text", "jsonl": "text", "json": "json", "csv": "csv", "html": "html"}
TEXT_NAMES = "md, txt, html, csv, json"
HIT_CHARS = 240
MAX_READ = 400
ARCHIVE_TZ = ZoneInfo("Europe/Vienna")  # the CET archive's `time_local` (the server converts it to UTC)

# The platform searches files in its generator's order, not alphabetically (observed on recorded searches).
SEARCH_FIRST = ["/AGENTS.md", "/runbook.md", "/standing_order.md", "/contacts.csv", "/fleet/agents.csv",
                "/fleet/registry_export.json", "/fleet/changes.md", "/fleet/tokens.csv", "/logs/audit/",
                "/logs/archive/", "/board/", "/fleet/token_fingerprints.csv"]

_JS_REGEX_ERRORS = [("unterminated character set", "Unterminated character class"),
                    ("missing ), unterminated subpattern", "Unterminated group"),
                    ("unbalanced parenthesis", "Unmatched ')'"), ("nothing to repeat", "Nothing to repeat"),
                    ("bad escape (end of pattern)", "\\ at end of pattern")]


class ToolError(Exception):
    """Becomes {ok: false, error}, like the real API."""


def _r1(x: float) -> str:
    return f"{math.floor(x * 10 + 0.5) / 10:.1f}"  # half-up like the platform


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{_r1(n / 1024)} KB"
    return f"{_r1(n / 1024 / 1024)} MB"


def norm_path(p: str | None) -> str:
    p = "/" + (p or "/").strip().lstrip("/")
    return p.rstrip("/") or "/" if p != "/" else "/"


def parse_ts(s) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _search_rank(path: str) -> tuple:
    for i, pre in enumerate(SEARCH_FIRST):
        if path == pre or (pre.endswith("/") and path.startswith(pre)):
            return (i, path)
    return (len(SEARCH_FIRST), path)


class LocalTools:
    def __init__(self, view: TaskView):
        self.v = view

    # --- files ---
    def _lines(self, p: str) -> list[str]:
        d = self.v.doc(p, parse_lazy=False)
        if d is None:
            raise ToolError(f"no such file: {p}")
        return d.lines or []

    def _size(self, p: str) -> int:
        return self.v.doc(p, parse_lazy=False).size

    def _describe(self, p: str) -> str:
        e = ext(p)
        bits = [FORMAT.get(e, e or "file")]
        if e == "pdf" and (n := self.v.doc(p, parse_lazy=False).pages) is not None:
            bits.append(f"{n}p")
        bits.append(fmt_size(self._size(p)))
        if e not in TEXT_EXT:
            bits.append("download")
        return ", ".join(bits)

    def _children(self, d: str) -> tuple[list[str], list[str]]:
        pre = "" if d == "/" else d
        dirs, files = set(), set()
        for p in self.v.paths():
            if p.startswith(pre + "/"):
                rest = p[len(pre) + 1:]
                (dirs.add(rest.split("/", 1)[0]) if "/" in rest else files.add(rest))
        return sorted(dirs), sorted(files)

    def _is_dir(self, d: str) -> bool:
        return d == "/" or any(p.startswith(d + "/") for p in self.v.paths())

    # --- tools ---
    def tree(self, path: str = "/", depth: int = 3) -> str:
        root = norm_path(path)
        if not self._is_dir(root):
            raise ToolError(f"not a directory: {root}")
        depth = int(depth if depth is not None else 3)
        out = [root]

        def walk(d: str, prefix: str, level: int) -> None:
            dirs, files = self._children(d)
            entries = [(n, True) for n in dirs] + [(n, False) for n in files]
            for i, (name, is_dir) in enumerate(entries):
                last = i == len(entries) - 1
                branch = "└── " if last else "├── "
                full = d.rstrip("/") + "/" + name
                if is_dir:
                    out.append(f"{prefix}{branch}{name}/")
                    if level < depth:
                        walk(full, prefix + ("    " if last else "│   "), level + 1)
                else:
                    out.append(f"{prefix}{branch}{name} [{self._describe(full)}]")

        walk(root, "", 1)
        return "\n".join(out)

    def list(self, path: str = "/") -> dict:
        d = norm_path(path)
        if not self._is_dir(d):
            raise ToolError(f"directory not found: {d}")
        dirs, files = self._children(d)
        base = "" if d == "/" else d
        entries: list[dict] = [{"name": n + "/", "type": "dir"} for n in dirs]
        for n in files:
            p = f"{base}/{n}"
            item = {"name": n, "type": "file", "format": FORMAT.get(ext(p), ext(p)), "size": self._size(p)}
            if ext(p) == "pdf" and (pg := self.v.doc(p, parse_lazy=False).pages) is not None:
                item["pages"] = pg
            item["readable"] = ext(p) in TEXT_EXT
            entries.append(item)
        return {"path": d, "entries": entries}

    def read(self, path: str = "", start_line=None, end_line=None) -> dict:
        if not path:
            raise ToolError("missing required argument: path")
        p = norm_path(path)
        if p not in self.v.files:
            if self._is_dir(p):
                raise ToolError(f"{p} is a directory; use list")
            raise ToolError(f"file not found: {p}")
        if ext(p) not in TEXT_EXT:
            raise ToolError(f"cannot read {p}: this is a {FORMAT.get(ext(p), ext(p))} file; the read/search tools only "
                            f"serve plain text ({TEXT_NAMES}). Download the raw file and parse it locally (e.g. pypdf / "
                            f"pdfplumber). Raw bytes: GET /api/v1/runs/{{run_id}}/tasks/{{task_id}}/files{p} "
                            f"(or workspace.zip for the whole workspace).")
        lines = self._lines(p)
        total = len(lines)
        s = max(1, int(start_line or 1))
        e = min(total, int(end_line) if end_line else total, s + MAX_READ - 1)
        w = len(str(e))
        out = {"path": p, "kind": FORMAT.get(ext(p), ext(p)), "total_lines": total, "start_line": s, "end_line": e,
               "text": "\n".join(f"{i:>{w}}| {lines[i - 1]}" for i in range(s, e + 1))}
        if e < total and (not end_line or int(end_line) > e):
            out["truncated"] = True
            out["hint"] = f"use start_line={e + 1} to continue"
        return out

    def search(self, pattern: str = "", path: str = "/", regex: bool = False, limit: int = 30) -> dict:
        if not pattern:
            raise ToolError("missing required argument: pattern")
        limit = max(1, min(100, int(limit or 30)))
        try:
            rx = re.compile(pattern if regex else re.escape(pattern), re.IGNORECASE)
        except re.error as e:
            msg = next((js for py, js in _JS_REGEX_ERRORS if str(e).startswith(py)), str(e))
            raise ToolError(f"invalid regex: Invalid regular expression: /{pattern}/i: {msg}")
        scope = norm_path(path)
        hits: list[dict] = []
        total = skipped = 0
        for p in sorted(self.v.paths(), key=_search_rank):
            if not (p == scope or scope == "/" or p.startswith(scope + "/")):
                continue
            if ext(p) not in TEXT_EXT:
                skipped += 1
                continue
            for i, line in enumerate(self._lines(p), 1):
                if rx.search(line):
                    total += 1
                    if len(hits) < limit:
                        hits.append({"path": p, "line": i,
                                     "text": line if len(line) <= HIT_CHARS else line[:HIT_CHARS] + "…"})
        out: dict = {"pattern": pattern, "hits": hits, "total_hits": total}
        if total > len(hits):
            out["truncated"] = True
        if skipped:
            out["note"] = (f"search covers only plain-text files ({TEXT_NAMES}); {skipped} binary file(s) in scope "
                           f"were skipped — download and parse them locally.")
        return out

    def events(self) -> list[dict]:
        """Audit-log events (jsonl under /logs/audit/) and the CET archive (csv under /logs/archive/), in time order."""
        ev = []
        for p in self.v.paths():
            if p.startswith("/logs/audit/") and ext(p) == "jsonl":
                for i, line in enumerate(self._lines(p), 1):
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    ev.append({"file": p, "line": i, **e, "ts": e.get("ts"), "_raw": line})
            elif p.startswith("/logs/archive/") and ext(p) == "csv":
                rows = list(csv.reader(io.StringIO("\n".join(self._lines(p)))))
                if not rows:
                    continue
                head = rows[0]
                for i, row in enumerate(rows[1:], 2):
                    rec = {k: v for k, v in zip(head, row) if v != ""}
                    local = rec.pop("time_local", None)
                    try:
                        ts = datetime.strptime(local, "%d.%m.%Y %H:%M:%S").replace(tzinfo=ARCHIVE_TZ) \
                            .astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    except (TypeError, ValueError):
                        ts = None
                    ev.append({"file": p, "line": i, "ts": ts, **rec, "_raw": ",".join(row)})
        ev.sort(key=lambda e: (e["ts"] or "", e["file"], e["line"]))
        return ev

    def query_logs(self, agent=None, action=None, target=None, trace=None, text=None, since=None, until=None,
                   limit=50, offset=0) -> dict:
        limit, offset = max(1, min(200, int(limit or 50))), max(0, int(offset or 0))
        s, u = parse_ts(since) if since else None, parse_ts(until) if until else None
        if since and not s:
            raise ToolError(f"cannot parse 'since': {since}")
        if until and not u:
            raise ToolError(f"cannot parse 'until': {until}")
        hits = []
        for e in self.events():
            t = parse_ts(e["ts"])
            if agent and e.get("agent") != agent:
                continue
            if action and e.get("action") != action:
                continue
            if target and target.lower() not in str(e.get("target", "")).lower():
                continue
            if trace and e.get("trace") != trace:
                continue
            if text and text.lower() not in e["_raw"].lower():
                continue
            if s and (not t or t < s):
                continue
            if u and (not t or t >= u):
                continue
            hits.append({k: v for k, v in e.items() if k != "_raw"})
        page = hits[offset:offset + limit]
        out = {"total": len(hits), "offset": offset, "returned": len(page), "events": page}
        if offset + len(page) < len(hits):
            out["hint"] = f"use offset={offset + len(page)} for more"
        return out


def call(view: TaskView, tool: str, args: dict) -> dict:
    """The API envelope for a local read-only tool."""
    try:
        return {"ok": True, "result": getattr(LocalTools(view), tool)(**(args or {}))}
    except ToolError as e:
        return {"ok": False, "error": str(e)}
    except TypeError as e:
        return {"ok": False, "error": f"bad arguments: {e}"}
