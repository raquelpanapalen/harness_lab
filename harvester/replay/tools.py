"""Task-API tools re-implemented over a harvested workspace (files by sha256 + an in-memory overlay for writes).

Formats follow GET /tools and the docs where they say something; where they are silent the choice is marked GUESS.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TEXT_EXT = {"md", "txt", "csv", "json", "jsonl", "html"}
FORMAT = {"md": "markdown", "txt": "text", "jsonl": "text", "json": "json", "csv": "csv", "html": "html"}
TEXT_NAMES = "md, txt, html, csv, json"
HIT_CHARS = 240
MAX_READ = 400
VIENNA = ZoneInfo("Europe/Vienna")


class ToolError(Exception):
    """Becomes {ok: false, error} with HTTP 200, like the real API."""


def ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _r1(x: float) -> str:
    return f"{math.floor(x * 10 + 0.5) / 10:.1f}"  # half-up like the platform (7424 B -> 7.3 KB, not 7.2)


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{_r1(n / 1024)} KB"
    return f"{_r1(n / 1024 / 1024)} MB"


def norm_path(p: str | None) -> str:
    p = "/" + (p or "/").strip().lstrip("/")
    return p.rstrip("/") or "/" if p != "/" else "/"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s) -> datetime | None:
    if not isinstance(s, str):
        return None
    s = s.strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def parse_tree_pages(tree: str) -> dict[str, int | None]:
    """path -> page count (or None) for every PDF line of a recorded `tree` result."""
    out: dict[str, int | None] = {}
    stack: list[str] = []
    for line in tree.splitlines()[1:]:
        m = re.match(r"^((?:│   |    )*)(?:├── |└── )(.*)$", line)
        if not m:
            continue
        level = len(m.group(1)) // 4
        name = m.group(2)
        stack = stack[:level]
        if name.endswith("/"):
            stack.append(name[:-1])
            continue
        fm = re.match(r"^(.*) \[([^\]]*)\]$", name)
        if fm and fm.group(1).lower().endswith(".pdf"):
            pg = re.search(r"\b(\d+)p\b", fm.group(2))
            out["/" + "/".join(stack + [fm.group(1)])] = int(pg.group(1)) if pg else None
    return out


# The platform searches files in its generator's order, not alphabetically. Observed (approximate): operator files,
# then the fleet registry files, then the gateway logs, then the board, then everything else.
SEARCH_FIRST = ["/AGENTS.md", "/runbook.md", "/standing_order.md", "/contacts.csv", "/fleet/agents.csv",
                "/fleet/registry_export.json", "/fleet/changes.md", "/fleet/tokens.csv", "/logs/audit/",
                "/logs/archive/", "/board/", "/fleet/token_fingerprints.csv"]


def search_rank(path: str) -> tuple:
    for i, pre in enumerate(SEARCH_FIRST):
        if path == pre or (pre.endswith("/") and path.startswith(pre)):
            return (i, path)
    return (len(SEARCH_FIRST), path)


_JS_REGEX_ERRORS = [("unterminated character set", "Unterminated character class"),
                    ("missing ), unterminated subpattern", "Unterminated group"),
                    ("unbalanced parenthesis", "Unmatched ')'"), ("nothing to repeat", "Nothing to repeat"),
                    ("bad escape (end of pattern)", "\\ at end of pattern")]


def js_regex_error(e: re.error) -> str:
    msg = str(e)
    return next((js for py, js in _JS_REGEX_ERRORS if msg.startswith(py)), msg)


class Workspace:
    """One task's workspace: base files (path -> sha256) from the harvest plus an overlay of changed files."""

    def __init__(self, files: dict[str, str], blob_dir: Path, page_counts: dict, intel: dict | None,
                 recorded_tree: str | None = None):
        self.base = dict(files)
        # The real server omits page counts for some PDFs; take them from the recorded `tree` when we have it.
        self.known_pages = parse_tree_pages(recorded_tree) if recorded_tree else {}
        self.blob_dir = blob_dir
        self.overlay: dict[str, bytes] = {}
        self.page_counts = page_counts  # shared sha -> pages|None cache
        self.intel = {k.lower(): v for k, v in (intel or {}).items()}
        self.actions: list[dict] = []  # every state-changing call, for the grader
        self.authorized: set[str] | None = None  # "write", "send_message", "containment:<op>"; None = anything
        self._containment_path = self._find_containment()
        self.initial_containment = self._containment()

    # --- files ---------------------------------------------------------------------------------------
    def paths(self) -> list[str]:
        return sorted(set(self.base) | set(self.overlay))

    def data(self, path: str) -> bytes:
        if path in self.overlay:
            return self.overlay[path]
        if path in self.base:
            return (self.blob_dir / self.base[path]).read_bytes()
        raise ToolError(f"no such file: {path}")

    def size(self, path: str) -> int:
        if path in self.overlay:
            return len(self.overlay[path])
        return (self.blob_dir / self.base[path]).stat().st_size

    def text(self, path: str) -> str:
        return self.data(path).decode("utf-8", errors="replace")

    def pages(self, path: str) -> int | None:
        if ext(path) != "pdf" or path in self.overlay:
            return None
        if path in self.known_pages:
            return self.known_pages[path]
        sha = self.base[path]
        if sha not in self.page_counts:
            try:
                from pypdf import PdfReader
                self.page_counts[sha] = len(PdfReader(str(self.blob_dir / sha)).pages)
            except Exception:
                self.page_counts[sha] = None
        return self.page_counts[sha]

    def describe(self, path: str) -> str:
        e = ext(path)
        bits = [FORMAT.get(e, e or "file")]
        if (p := self.pages(path)) is not None:
            bits.append(f"{p}p")
        bits.append(fmt_size(self.size(path)))
        if e not in TEXT_EXT:
            bits.append("download")
        return ", ".join(bits)

    def _children(self, d: str) -> tuple[list[str], list[str]]:
        pre = "" if d == "/" else d
        dirs, files = set(), set()
        for p in self.paths():
            if not p.startswith(pre + "/"):
                continue
            rest = p[len(pre) + 1:]
            (dirs.add(rest.split("/", 1)[0]) if "/" in rest else files.add(rest))
        return sorted(dirs), sorted(files)

    def _is_dir(self, d: str) -> bool:
        return d == "/" or any(p.startswith(d + "/") for p in self.paths())

    # --- read-only tools -----------------------------------------------------------------------------
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
                full = (d.rstrip("/") + "/" + name)
                if is_dir:
                    out.append(f"{prefix}{branch}{name}/")
                    if level < depth:
                        walk(full, prefix + ("    " if last else "│   "), level + 1)
                else:
                    out.append(f"{prefix}{branch}{name} [{self.describe(full)}]")

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
            item = {"name": n, "type": "file", "format": FORMAT.get(ext(p), ext(p)), "size": self.size(p)}
            if (pg := self.pages(p)) is not None:
                item["pages"] = pg
            item["readable"] = ext(p) in TEXT_EXT
            entries.append(item)
        return {"path": d, "entries": entries}

    def read(self, path: str = "", start_line=None, end_line=None) -> dict:
        if not path:
            raise ToolError("missing required argument: path")
        p = norm_path(path)
        if p not in self.base and p not in self.overlay:
            if self._is_dir(p):
                raise ToolError(f"{p} is a directory; use list")
            raise ToolError(f"file not found: {p}")
        if ext(p) not in TEXT_EXT:
            raise ToolError(f"cannot read {p}: this is a {FORMAT.get(ext(p), ext(p))} file; the read/search tools only "
                            f"serve plain text ({TEXT_NAMES}). Download the raw file and parse it locally (e.g. pypdf / "
                            f"pdfplumber). Raw bytes: GET /api/v1/runs/{{run_id}}/tasks/{{task_id}}/files{p} "
                            f"(or workspace.zip for the whole workspace).")
        lines = self.text(p).splitlines()
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
            raise ToolError(f"invalid regex: Invalid regular expression: /{pattern}/i: {js_regex_error(e)}")
        scope = norm_path(path)
        hits: list[dict] = []
        total, skipped = 0, 0
        for p in sorted(self.paths(), key=search_rank):
            if not (p == scope or scope == "/" or p.startswith(scope + "/")):
                continue
            if ext(p) not in TEXT_EXT:
                skipped += 1
                continue
            for i, line in enumerate(self.text(p).splitlines(), 1):
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

    def _events(self) -> list[dict]:
        ev = []
        for p in self.paths():
            if p.startswith("/logs/audit/") and ext(p) == "jsonl":
                for i, line in enumerate(self.text(p).splitlines(), 1):
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    ev.append({"file": p, "line": i, **e, "ts": e.get("ts"), "_raw": line})
            elif p.startswith("/logs/archive/") and ext(p) == "csv":
                rows = list(csv.reader(io.StringIO(self.text(p))))
                if not rows:
                    continue
                head = rows[0]
                for i, row in enumerate(rows[1:], 2):
                    rec = {k: v for k, v in zip(head, row) if v != ""}
                    local = rec.pop("time_local", None)
                    try:
                        ts = datetime.strptime(local, "%d.%m.%Y %H:%M:%S").replace(tzinfo=VIENNA) \
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
        for e in self._events():
            t = parse_ts(e["ts"])
            if agent and e.get("agent") != agent: continue
            if action and e.get("action") != action: continue
            if target and target.lower() not in str(e.get("target", "")).lower(): continue
            if trace and e.get("trace") != trace: continue
            if text and text.lower() not in e["_raw"].lower(): continue
            if s and (not t or t < s): continue
            if u and (not t or t >= u): continue
            hits.append({k: v for k, v in e.items() if k != "_raw"})
        page = hits[offset:offset + limit]
        out = {"total": len(hits), "offset": offset, "returned": len(page), "events": page}
        if offset + len(page) < len(hits):
            out["hint"] = f"use offset={offset + len(page)} for more"
        return out

    def _registry(self) -> dict:
        for p in self.paths():
            if p.startswith("/fleet/") and "registry" in p and ext(p) == "json":
                return json.loads(self.text(p))
        raise ToolError("registry export not found")

    def access_path(self, **a) -> dict:
        src, to = a.get("from"), a.get("to")
        if not src:
            raise ToolError("missing required argument: from")
        if not to:
            raise ToolError("missing required argument: to")
        reg = self._registry()
        classes = reg.get("scope_classes", {})
        agents = {x["id"]: x for x in reg.get("agents", [])}
        if src not in agents:
            raise ToolError(f'unknown agent "{src}"')
        class_names = [c for c in ("private_data", "untrusted_input", "outbound") if c in set(classes.values())]
        if to not in class_names and to not in classes and \
                not any(to in x.get("scopes", []) for x in agents.values()):
            raise ToolError(f'unknown scope or class "{to}". Classes: {", ".join(class_names)}')
        edges = {k: list(v.get("delegates_to", [])) for k, v in agents.items()}
        scopes = {k: list(v.get("scopes", [])) for k, v in agents.items()}
        for f, t in a.get("added") or []:
            edges.setdefault(f, [])
            if t not in edges[f]:
                edges[f].append(t)
        for f, t in a.get("removed") or []:
            edges[f] = [x for x in edges.get(f, []) if x != t]
        for ag, sc in a.get("granted") or []:
            scopes.setdefault(ag, [])
            if sc not in scopes[ag]:
                scopes[ag].append(sc)
        for ag, sc in a.get("revoked") or []:
            scopes[ag] = [x for x in scopes.get(ag, []) if x != sc]
        excl = set(a.get("exclude") or [])
        note = (f"Computed on the registry export of {reg.get('exported_at')}. Later changes are only included if "
                f"you passed them.")

        def holding(ag: str) -> list[str]:
            return [sc for sc in scopes.get(ag, []) if sc == to or classes.get(sc) == to]

        q, prev = deque([src] if src not in excl else []), {src: None}
        while q:
            cur = q.popleft()
            if held := holding(cur):
                chain = []
                node = cur
                while node is not None:
                    chain.append(node)
                    node = prev[node]
                chain.reverse()
                return {"found": True, "from": src, "to": to, "chain": chain, "holder": cur, "holder_scopes": held,
                        "hops": len(chain) - 1, "note": note}
            for nxt in edges.get(cur, []):
                if nxt not in prev and nxt not in excl:
                    prev[nxt] = cur
                    q.append(nxt)
        return {"found": False, "from": src, "to": to, "message": "no delegation chain reaches an agent with this scope",
                "note": note}

    def threat_intel(self, indicator: str = "") -> dict:
        if not indicator:
            raise ToolError("missing required argument: indicator")
        hit = self.intel.get(indicator.strip().lower())
        if hit is None:
            raise ToolError(f"replay: no recorded threat-intel answer for {indicator!r} "
                            "(harvest new runs with --record-intel)")
        if isinstance(hit, dict) and "ok" in hit:
            if not hit["ok"]:
                raise ToolError(hit.get("error", "error"))
            return hit["result"]
        return hit

    # --- containment state ---------------------------------------------------------------------------
    def _find_containment(self) -> str | None:
        cands = [p for p in self.paths() if ext(p) == "json" and p.startswith("/plans/")]
        for p in cands:
            try:
                if "quarantined" in json.loads(self.text(p)):
                    return p
            except ValueError:
                pass
        return None

    def _containment(self) -> dict:
        if not self._containment_path:
            return {"quarantined": [], "revoked_tokens": [], "rotations": [], "blocked_domains": [], "notes": []}
        return json.loads(self.text(self._containment_path))

    def get_containment(self) -> dict:
        return self._containment()

    def containment(self, ops=None) -> dict:
        if not isinstance(ops, list) or not ops:
            raise ToolError("ops must be a non-empty array")
        st = json.loads(json.dumps(self._containment()))
        reg_agents = {x["id"] for x in self._registry().get("agents", [])}
        tokens = self.known_tokens()
        # Real server (12 recorded responses): new entries carry the scenario's "now" from /AGENTS.md and
        # "by": "you"; version +1 and updated_at = now; blocked domains are plain strings; any `at` is accepted
        # for a rotation. Errors: op <i>: unknown <thing> "<value>".
        stamp = self.scenario_now()
        q = lambda v: json.dumps("" if v is None else str(v))  # noqa: E731
        for i, o in enumerate(ops):  # validate + apply on a copy: atomic
            o = o if isinstance(o, dict) else {}
            op = o.get("op")
            if op == "quarantine_agent":
                ag = o.get("agent")
                if ag not in reg_agents:
                    raise ToolError(f"op {i}: unknown agent {q(ag)}")
                if o.get("stand_in") is not None and o["stand_in"] not in reg_agents:
                    raise ToolError(f"op {i}: unknown stand_in {q(o['stand_in'])}")
                st["quarantined"] = [x for x in st.get("quarantined", []) if x.get("agent") != ag]
                st["quarantined"].append({"agent": ag, "at": stamp, "stand_in": o.get("stand_in"), "by": "you"})
            elif op == "release_agent":
                if o.get("agent") not in reg_agents:
                    raise ToolError(f"op {i}: unknown agent {q(o.get('agent'))}")
                st["quarantined"] = [x for x in st.get("quarantined", []) if x.get("agent") != o.get("agent")]
            elif op in ("revoke_token", "rotate_token"):
                if tokens and o.get("token") not in tokens:
                    raise ToolError(f"op {i}: unknown token {q(o.get('token'))}")
                if op == "revoke_token":
                    st.setdefault("revoked_tokens", []).append({"token": o["token"], "at": stamp})
                else:
                    if not o.get("at"):
                        raise ToolError(f"op {i}: rotate_token needs at")  # GUESS: wording; real response unseen
                    st["rotations"] = [r for r in st.get("rotations", []) if r.get("token") != o["token"]]
                    st["rotations"].append({"token": o["token"], "at": o["at"]})
            elif op == "block_domain":
                if not o.get("domain"):
                    raise ToolError(f"op {i}: unknown domain {q(o.get('domain'))}")  # GUESS: wording
                doms = [d.get("domain") if isinstance(d, dict) else d for d in st.get("blocked_domains", [])]
                if o["domain"] not in doms:
                    st.setdefault("blocked_domains", []).append(o["domain"])
            elif op == "add_note":
                st.setdefault("notes", []).append(str(o.get("text", "")))
            else:
                raise ToolError(f"op {i}: unknown op {q(op)}")
        st["version"] = int(st.get("version") or 0) + 1
        st["updated_at"] = stamp
        st = _real_order(st)
        if self._containment_path:
            self.overlay[self._containment_path] = json.dumps(st, indent=2, ensure_ascii=False).encode()
        return {"applied": len(ops), "state": st}

    def scenario_now(self) -> str:
        """The scenario's clock ("Current time ("now"): ..." in /AGENTS.md); wall-clock time if it has none."""
        try:
            m = re.search(r'Current time \("now"\):\s*(\d{4}-\d\d-\d\dT[\d:]+Z)', self.text("/AGENTS.md"))
        except Exception:  # noqa: BLE001 - no AGENTS.md
            m = None
        return m.group(1) if m else (self.initial_containment.get("updated_at") or now_utc())

    def known_tokens(self) -> set[str]:
        """Token ids of the fleet (the real server rejects rotating / revoking an unknown token)."""
        out: set[str] = set()
        for p in self.paths():
            if p.startswith("/fleet/") and p.endswith(".csv") and "token" in p.rsplit("/", 1)[-1]:
                out |= set(re.findall(r"\bTOK-[A-Z0-9]+-\d+\b", self.text(p)))
        return out

    def send_message(self, to: str = "", subject: str = "", body: str = "") -> dict:
        if not to or not subject or not body:
            raise ToolError("to, subject and body are required")
        n = sum(1 for a in self.actions if a["tool"] == "send_message" and a["ok"]) + 1
        return {"sent": True, "to": to, "message_id": f"MSG-{n}",
                "recipient_known": to.strip().casefold() in self.contact_addresses()}

    def contact_addresses(self) -> set[str]:
        """Every address in contacts.csv, verified or not (send_message's `recipient_known`; GUESS: all rows)."""
        out: set[str] = set()
        for p in self.paths():
            if p.rsplit("/", 1)[-1].lower() == "contacts.csv":
                out |= {m.casefold() for m in re.findall(r"[^@\s,;\"]+@[^@\s,;\"]+", self.text(p))}
        return out

    def write(self, path: str = "", content: str = "") -> dict:
        p = norm_path(path)
        if not p.startswith("/drafts/") or ".." in p:
            raise ToolError("write is only allowed under /drafts/")
        if content is None or content == "":
            raise ToolError("content is required")
        self.overlay[p] = str(content).encode()
        return {"written": p, "bytes": len(self.overlay[p])}

    def sent_messages(self) -> list[dict]:
        return [a["args"] for a in self.actions if a["tool"] == "send_message" and a["ok"]]

    def verified_addresses(self) -> set[str]:
        """E-mail addresses of verified contacts. Column names vary per surface (via/transport/...), so the
        address column is found by content and the verified column by a name containing 'verif'."""
        out: set[str] = set()
        for p in self.paths():
            if p.rsplit("/", 1)[-1].lower() != "contacts.csv":
                continue
            rows = list(csv.DictReader(io.StringIO(self.text(p))))
            vcol = next((k for k in (rows[0] if rows else {}) if k and "verif" in k.lower()), None)
            for r in rows:
                if vcol and str(r.get(vcol, "")).strip().lower() not in ("yes", "y", "true", "1", "✓", "verified"):
                    continue
                for v in r.values():
                    if isinstance(v, str) and re.fullmatch(r"[^@\s,;]+@[^@\s,;]+", v.strip()):
                        out.add(v.strip().casefold())
        return out


STATE_CHANGING = {"containment", "send_message", "write"}
TOOL_ORDER = ["tree", "list", "read", "search", "query_logs", "access_path", "threat_intel", "get_containment",
              "containment", "send_message", "write"]
TOOLS = {"tree", "list", "read", "search", "query_logs", "access_path", "threat_intel", "get_containment",
         "containment", "send_message", "write"}


_STATE_ORDER = ["notes", "version", "incident", "rotations", "updated_at", "quarantined", "revoked_tokens",
                "blocked_domains"]
_OLD_ENTRY_ORDER = ["at", "by", "agent", "stand_in"]


def _real_order(st: dict) -> dict:
    """Key order of the real containment response (the state is rebuilt there; entries already in the plan file
    come back as at/by/agent/stand_in, new ones as agent/at/stand_in/by)."""
    out = {k: st[k] for k in _STATE_ORDER if k in st}
    out.update({k: v for k, v in st.items() if k not in out})
    out["quarantined"] = [
        {**{k: e[k] for k in _OLD_ENTRY_ORDER if k in e}, **e} if e.get("by") != "you" else e
        for e in out.get("quarantined", [])]
    return out


def authorised(allowed: set[str], tool: str, args: dict) -> bool:
    if tool != "containment":
        return tool in allowed
    ops = args.get("ops") if isinstance(args, dict) else None
    if not isinstance(ops, list) or not ops:
        return any(a.startswith("containment:") for a in allowed)  # let the tool report the bad arguments
    return all(f"containment:{(o or {}).get('op') if isinstance(o, dict) else None}" in allowed for o in ops)


def call(ws: Workspace, tool: str, args: dict) -> dict:
    """Run a tool; returns the API envelope. State-changing calls are recorded even when they fail."""
    if tool not in TOOLS:
        return {"ok": False, "error": f'unknown tool "{tool}". Available: ' + ", ".join(TOOL_ORDER)}
    if tool in STATE_CHANGING and ws.authorized is not None and not authorised(ws.authorized, tool, args):
        # Real server: refused at call time, and the attempt still fails the task (constraint unauthorized_action).
        ws.actions.append({"tool": tool, "args": args, "ok": False, "n": len(ws.actions) + 1, "unauthorized": True})
        return {"ok": False, "error": "this task does not authorise that state-changing action"}
    try:
        if tool == "access_path":
            res = ws.access_path(**args)
        else:
            res = getattr(ws, tool)(**args)
        out = {"ok": True, "result": res}
    except ToolError as e:
        out = {"ok": False, "error": str(e)}
    except TypeError as e:
        out = {"ok": False, "error": f"bad arguments: {e}"}
    if tool in STATE_CHANGING:
        ws.actions.append({"tool": tool, "args": args, "ok": out["ok"], "n": len(ws.actions) + 1})
    return out
