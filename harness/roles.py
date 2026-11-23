"""Workspace file roles (build ticket 17): every input file is found by what it is, never by its path or name.

    r = roles_of(view)          # one task's workspace (harness.index.TaskView), cached per view
    r.audit, r.archive          # gateway logs: jsonl events (ts/agent/action), the local-time csv archive
    r.one("changes")            # the IAM change log path, or None when missing or ambiguous
    r.runbook, r.signed_order   # operator files named by /AGENTS.md; the signed sheet the runbook points to

Each blob gets its **tags** from its content alone (cached by sha256 for the whole run):

| tag | recognised by |
|---|---|
| audit | jsonl whose rows are events with `ts`, `agent`, `action` |
| board | jsonl of posts with `id`, `author`, `text` |
| archive | csv with `agent` and `action` columns and a `dd.mm.yyyy hh:mm` time column |
| changes | markdown table with a change and an agent column (the IAM change log) |
| artifact_index | markdown table with a copy and an original-location column (the quarantine index) |
| containment_state | json object with a `quarantined` list |
| registry | json object whose `agents` are records with `id` and scopes or delegations |
| contacts | csv with a verified column and e-mail addresses |
| fingerprints | csv with a sha256 column |
| tokens | csv with an owner and a consumer / used-by column |
| access_log | csv with request_id, ts, actor, action, scope_class and result columns |
| access_log_policy | text with numbered "Its scope_class is" clauses |
| pending_access | csv with request_id, filed_by and change columns |
| mcp_manifest | json object with a `name` and a `tools` list |
| ticket | text whose front matter (or first lines) has a `status:` field |
| message | text whose front matter has a `from:` header |

`/AGENTS.md` (fixed by the Task API docs) names the operator files; the paths that operator files mention are hints
that settle a tag matching several files. `one(tag)` never guesses: several candidates and no hint → None, and the
caller raises its `Precondition` (the task goes to the loop) or fails closed.
"""
from __future__ import annotations

import csv
import io
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from .index import IMAGE_EXT, TEXT_EXT, TaskView, ext

AGENTS_MD = "/AGENTS.md"
PATH_RX = re.compile(r"(/[\w./-]*[\w])")
LOCAL_TIME = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4} \d{1,2}:\d{2}")
FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
SCAN_ROWS = 20          # jsonl rows / csv rows looked at per blob

_lock = threading.Lock()


# --- per-blob tags --------------------------------------------------------------------------------------------------
def _csv_head(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    rows = list(csv.reader(io.StringIO("\n".join(lines[:SCAN_ROWS + 1]))))
    if not rows:
        return [], []
    return [h.strip().lower() for h in rows[0]], rows[1:]


def _has(head: list[str], *keys: str) -> bool:
    return any(k in h for h in head for k in keys)


def _jsonl_rows(lines: list[str]) -> list[dict]:
    out = []
    for line in lines[:SCAN_ROWS]:
        try:
            v = json.loads(line)
        except ValueError:
            continue
        if isinstance(v, dict):
            out.append(v)
    return out


def _md_table_heads(lines: list[str]) -> list[list[str]]:
    """Header rows of every markdown table (a `|` row followed by a `|---|` row)."""
    out = []
    for a, b in zip(lines, lines[1:]):
        if a.strip().startswith("|") and re.fullmatch(r"\|?\s*:?-{3,}.*", b.strip()):
            out.append([c.strip().lower() for c in a.strip().strip("|").split("|")])
    return out


def front_matter(text: str) -> dict[str, str]:
    m = FRONT.match(text or "")
    meta: dict[str, str] = {}
    for line in (m.group(1).splitlines() if m else []):
        k, sep, v = line.partition(":")
        if sep:
            meta[k.strip().lower()] = v.strip()
    return meta


def blob_tags(path: str, lines: list[str] | None) -> frozenset[str]:
    """The roles a text blob can play, from its content (the path only gives the format)."""
    e = ext(path)
    if lines is None or e not in TEXT_EXT:
        return frozenset()
    tags: set[str] = set()
    if e == "jsonl":
        rows = _jsonl_rows(lines)
        if rows:
            share = lambda keys: sum(all(k in r for k in keys) for r in rows) / len(rows)  # noqa: E731
            if share(("ts", "agent", "action")) >= 0.8:
                tags.add("audit")
            elif share(("id", "author", "text")) >= 0.8:
                tags.add("board")
    elif e == "csv":
        head, rows = _csv_head(lines)
        if head:
            if "agent" in head and "action" in head and rows and any(
                    LOCAL_TIME.match(c.strip()) for r in rows[:5] for c in r):
                tags.add("archive")
            if _has(head, "verif") and any("@" in c for r in rows for c in r):
                tags.add("contacts")
            if _has(head, "sha256"):
                tags.add("fingerprints")
            if _has(head, "owner") and _has(head, "consumer", "used_by", "used by"):
                tags.add("tokens")
            if {"request_id", "ts", "actor", "action", "scope_class", "result"} <= set(head):
                tags.add("access_log")
            if "request_id" in head and _has(head, "filed_by", "filed by") and _has(head, "change"):
                tags.add("pending_access")
    elif e == "json":
        try:
            d = json.loads("\n".join(lines))
        except ValueError:
            d = None
        if isinstance(d, dict):
            if isinstance(d.get("quarantined"), list):
                tags.add("containment_state")
            ag = d.get("agents")
            if isinstance(ag, list) and ag and all(isinstance(a, dict) and "id" in a for a in ag) and any(
                    "scopes" in a or "delegates_to" in a for a in ag):
                tags.add("registry")
            if isinstance(d.get("name"), str) and isinstance(d.get("tools"), list):
                tags.add("mcp_manifest")
    if e in ("md", "txt"):
        for head in _md_table_heads(lines):
            if _has(head, "change") and _has(head, "agent"):
                tags.add("changes")
            if _has(head, "copy") and _has(head, "original"):
                tags.add("artifact_index")
        if re.search(r"(?im)^\s*\d+\.\s+its\s+scope_class\s+is\b", "\n".join(lines)):
            tags.add("access_log_policy")
    if e in ("md", "txt", "eml"):
        meta = front_matter("\n".join(lines[:40]))
        if "status" in meta:
            tags.add("ticket")
        if "from" in meta:
            tags.add("message")
    return frozenset(tags)


def ids_in(text: str, ids) -> list[str]:
    """The known ids (agents, tokens, ...) that `text` names, in order of first appearance: whole ids only, so a
    prefix of a longer id is no match. The id formats come from the workspace, never from a fixed pattern."""
    hits = []
    for i in ids:
        if i and (m := re.search(rf"(?<![\w-]){re.escape(i)}(?![\w-])", text or "")):
            hits.append((m.start(), -len(i), i))
    return [i for _, _, i in sorted(hits)]


_TAGS: dict[str, frozenset[str]] = {}
_APPROVALS: dict[str, bool] = {}
TICKET_ID = re.compile(r"\b[A-Z]{2,5}-\d+\b")
NOT_APPROVED = re.compile(r"not approved|unapproved|reject|denied|withdrawn|revoked|pending|cancel", re.I)


def approves(text: str) -> bool:
    """A line that records a change ticket as approved (and does not withdraw it)."""
    return "approv" in text.lower() and not NOT_APPROVED.search(text) and bool(TICKET_ID.search(text))


def approval_doc(view: TaskView, path: str) -> bool:
    """A parsed document with a trusted line (no planted text) that approves a change ticket; cached per blob.
    PDFs are not parsed for this (they are parsed lazily, and no approval record came as a PDF on dev)."""
    sha = view.files.get(path)
    d = view.doc(path, parse_lazy=False) if sha else None
    if d is None or not d.parsed:
        return False
    with _lock:
        hit = _APPROVALS.get(sha)
    if hit is None:
        hit = any(u.kind != "file" and not u.untrusted and approves(u.text) for u in d.units)
        with _lock:
            _APPROVALS[sha] = hit
    return hit


def tags_of(view: TaskView, path: str) -> frozenset[str]:
    sha = view.files.get(path)
    if sha is None:
        return frozenset()
    with _lock:
        t = _TAGS.get(sha)
    if t is None:
        d = view.doc(path, parse_lazy=False)
        t = blob_tags(path, d.lines if d is not None else None)
        with _lock:
            _TAGS[sha] = t
    return t


# --- per-task roles -------------------------------------------------------------------------------------------------
_OPERATOR = re.compile(r"operator files?(?:[^.\n]|\.(?=\w))*", re.I)   # to the end of the sentence


def operator_files(agents_md: str, paths: set[str]) -> list[str]:
    """`/AGENTS.md` plus the files it calls operator files (only those sentences count), as far as they exist."""
    out = [AGENTS_MD]
    for sent in _OPERATOR.findall(agents_md or ""):
        out += [p for p in PATH_RX.findall(sent) if ext(p) in ("md", "txt") and p not in out]
    return [p for p in out if p in paths]


@dataclass
class Roles:
    view: TaskView
    by_tag: dict[str, list[str]] = field(default_factory=dict)
    operator: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)      # paths the operator files mention, in order
    memo: dict = field(default_factory=dict)

    # -- generic picks --
    def all(self, tag: str) -> list[str]:
        return list(self.by_tag.get(tag, []))

    def one(self, tag: str) -> str | None:
        """The single file with `tag`: the only candidate, else the one the operator files name, else None."""
        c = self.by_tag.get(tag, [])
        if len(c) == 1:
            return c[0]
        named = [p for p in c if p in self.hints]
        return named[0] if len(named) == 1 else None

    def text(self, path: str | None) -> str:
        d = self.view.doc(path, parse_lazy=False) if path else None
        return "\n".join(d.lines) if d is not None and d.lines is not None else ""

    # -- operator files --
    @property
    def runbook(self) -> str | None:
        """The operator file with the definitions: the first one /AGENTS.md names besides itself."""
        rest = [p for p in self.operator if p != AGENTS_MD]
        return rest[0] if rest else None

    def operator_texts(self) -> list[tuple[str, str]]:
        return [(p, self.text(p)) for p in self.operator]

    @property
    def signed_order(self) -> str | None:
        """The signed sheet in force: an image an operator file names in a sentence that says it is signed."""
        for _, t in self.operator_texts():
            for sent in re.split(r"(?<=[.!?])\s+|\n\s*\n", t):
                if re.search(r"\bsigned\b", sent, re.I):
                    for p in PATH_RX.findall(sent):
                        if ext(p) in IMAGE_EXT and p in self.view.files:
                            return p
        return None

    @property
    def order_copy(self) -> str | None:
        """The typed copy of the signed sheet (same name, text format): a decoy, never in force."""
        s = self.signed_order
        if not s:
            return None
        stem = s.rsplit(".", 1)[0]
        return next((p for p in self.view.paths() if p.rsplit(".", 1)[0] == stem and ext(p) in TEXT_EXT), None)

    # -- logs --
    @property
    def audit(self) -> list[str]:
        return self.all("audit")

    @property
    def archive(self) -> list[str]:
        return self.all("archive")

    @property
    def board(self) -> list[str]:
        return self.all("board")

    def local_zone(self) -> tzinfo | None:
        """The time zone of the rotated archive's local times, as the workspace states it: a note next to the archive,
        else the operator files; an IANA name ("Europe/Vienna") before a fixed offset ("CET = UTC+1"). None when
        nothing states it (archive times are then left unconverted, never guessed)."""
        if "_zone" in self.memo:
            return self.memo["_zone"]
        dirs = {p.rsplit("/", 1)[0] + "/" for p in self.archive}
        notes = [p for p in self.view.paths() if any(p.startswith(d) for d in dirs) and ext(p) in ("md", "txt")]
        texts = [self.text(p) for p in notes] + [t for _, t in self.operator_texts()]
        zone = None
        for t in texts:
            for name in re.findall(r"\b((?:Europe|America|Asia|Africa|Australia|Pacific|Atlantic|Indian)/[A-Za-z_]+)\b", t):
                try:
                    zone = ZoneInfo(name)
                    break
                except Exception:  # noqa: BLE001 - not a zone name
                    continue
            if zone:
                break
        if zone is None:
            for t in texts:
                m = re.search(r"\bUTC\s*([+\-−])\s*(\d{1,2})(?::(\d{2}))?", t)
                if m:
                    sign = -1 if m.group(1) in "-−" else 1
                    zone = timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0)))
                    break
        self.memo["_zone"] = zone
        return zone

    # -- registries --
    @property
    def registry(self) -> str | None:
        return self.one("registry")

    @property
    def contacts(self) -> str | None:
        return self.one("contacts")

    @property
    def changes(self) -> str | None:
        return self.one("changes")

    @property
    def artifact_index(self) -> str | None:
        return self.one("artifact_index")

    @property
    def artifact_dir(self) -> str | None:
        i = self.artifact_index
        return i.rsplit("/", 1)[0] + "/" if i else None

    @property
    def roster(self) -> str | None:
        t = self.roster_table()
        return t[0] if t else None

    def roster_table(self) -> tuple[str, str] | None:
        """(path, id column) of the roster: the csv with a critical / stand-in / owner column whose id column holds
        the registry's agents (else a header named id / agent); the shortest path when several qualify (as in v1)."""
        if "_roster" in self.memo:
            return self.memo["_roster"]
        reg = self.registry
        known: set[str] = set()
        if reg:
            try:
                known = {a.get("id") for a in json.loads(self.text(reg)).get("agents", []) if a.get("id")}
            except (ValueError, AttributeError):
                known = set()
        best = None
        for p in self.view.paths():
            if ext(p) != "csv":
                continue
            d = self.view.doc(p, parse_lazy=False)
            if not d or not d.lines:
                continue
            head = [h.strip().lower() for h in next(csv.reader([d.lines[0]]), [])]
            if not any(k in h for h in head for k in ("stand_in", "critical", "responsible", "owner")):
                continue
            rows = list(csv.DictReader(io.StringIO("\n".join(d.lines))))
            keys = list(rows[0].keys()) if rows else []
            idcol = None
            if known and rows:
                shares = [(sum((r.get(k) or "").strip() in known for r in rows) / len(rows), k) for k in keys]
                s_, k_ = max(shares, default=(0, None))
                idcol = k_ if s_ >= 0.8 else None
            if idcol is None:
                idcol = next((k for k in keys if k and k.strip().lower() in ("id", "agent", "agent_id")), None)
            if idcol and (best is None or len(p) < len(best[0])):
                best = (p, idcol)
        self.memo["_roster"] = best
        return best

    @property
    def containment_states(self) -> list[str]:
        return self.all("containment_state")

    # -- access, MCP --
    @property
    def mcp_dir(self) -> str | None:
        """The directory that holds our MCP manifests (the only one with two or more). Quarantined copies of
        manifests (under the artifact index's directory) are evidence, not our servers."""
        dirs: dict[str, int] = {}
        ad = self.artifact_dir
        for p in self.all("mcp_manifest"):
            if ad and p.startswith(ad):
                continue
            d = p.rsplit("/", 1)[0] + "/"
            dirs[d] = dirs.get(d, 0) + 1
        cands = [d for d, n in dirs.items() if n >= 2]
        return cands[0] if len(cands) == 1 else None

    def mentioning(self, path: str, ext_: tuple[str, ...] = ("md", "txt")) -> list[str]:
        """Text files that mention `path`: neither operator files nor the tickets and messages that carry requests."""
        skip = set(self.operator) | set(self.all("ticket")) | set(self.all("message")) | {path}
        return [p for p in self.view.paths() if ext(p) in ext_ and p not in skip and path in self.text(p)]

    def agent_ids(self) -> set[str]:
        """Our agents' ids: the registry's agents and the roster's id column."""
        if "_agents" not in self.memo:
            ids: set[str] = set()
            if self.registry:
                try:
                    ids |= {a.get("id") for a in json.loads(self.text(self.registry)).get("agents", []) if a.get("id")}
                except (ValueError, AttributeError):
                    pass
            t = self.roster_table()
            if t:
                d = self.view.doc(t[0], parse_lazy=False)
                ids |= {(r.get(t[1]) or "").strip() for r in csv.DictReader(io.StringIO("\n".join(d.lines or [])))}
            self.memo["_agents"] = ids - {""}
        return self.memo["_agents"]

    @property
    def documents(self) -> list[str]:
        """Prose documents that may be tier-2 human records (the on-call handover, notes): md / txt / docx / odt with no
        role of their own, outside the artifact copies, and not an agent's self-report (a file named after one of our
        agents is tier 3)."""
        if "_docs" not in self.memo:
            tagged = {p for ps in self.by_tag.values() for p in ps}
            ad, ids = self.artifact_dir, self.agent_ids()
            self.memo["_docs"] = [p for p in self.view.paths() if ext(p) in ("md", "txt", "docx", "odt")
                                  and p not in tagged and p not in self.operator and not (ad and p.startswith(ad))
                                  and p.rsplit("/", 1)[-1].rsplit(".", 1)[0] not in ids]
        return self.memo["_docs"]

    @property
    def approval_sources(self) -> list[str]:
        """Tier-2 approval records besides the change log (the on-call handover, in any format and under any name):
        documents with a trusted line approving a change ticket. Never a log, a table or another role's file."""
        if "_approvals" not in self.memo:
            self.memo["_approvals"] = [p for p in self.documents if approval_doc(self.view, p)]
        return self.memo["_approvals"]

    # -- what the solvers' facts depend on (the facts cache key) --
    def fact_files(self) -> list[str]:
        out = set(self.operator) | set(self.audit) | set(self.archive) | set(self.containment_states)
        out |= {p for p in (self.registry, self.contacts, self.changes, self.artifact_index, self.roster,
                            self.signed_order) if p}
        out |= set(self.all("tokens")) | set(self.all("fingerprints")) | set(self.approval_sources)
        ad = self.artifact_dir
        out |= {p for p in self.view.paths() if (ad and p.startswith(ad)) or ext(p) in ("csv", "xlsx", "ods")}
        return sorted(out)

    # -- the platform's search order (tests/checks/check_index.py: byte-identical search output) --
    def search_order(self) -> list[str]:
        """Files and directory prefixes in the order the platform's search visits them first."""
        order: list[str] = [AGENTS_MD]
        order += [p for p in (self.runbook, self.order_copy, self.contacts, self.roster, self.registry,
                              self.changes, self.one("tokens")) if p]
        for group in (self.audit, self.archive, self.board):
            order += list(dict.fromkeys(p.rsplit("/", 1)[0] + "/" for p in group))
        order += [p for p in (self.one("fingerprints"),) if p]
        return list(dict.fromkeys(order))


def build(view: TaskView) -> Roles:
    paths = view.paths()
    r = Roles(view)
    for p in paths:
        for t in tags_of(view, p):
            r.by_tag.setdefault(t, []).append(p)
    pset = set(paths)
    r.operator = operator_files(r.text(AGENTS_MD), pset)
    if len(r.operator) <= 1:
        # /AGENTS.md names no operator file: the root-level text files, the one naming most workspace files first
        # (the runbook defines terms over the workspace; a typed copy of a signed sheet names none)
        root = [p for p in paths if p.count("/") == 1 and p != AGENTS_MD and ext(p) in ("md", "txt")]
        n = {p: sum(q in pset for q in set(PATH_RX.findall(r.text(p)))) for p in root}
        r.operator = ([AGENTS_MD] if AGENTS_MD in pset else []) + sorted(root, key=lambda p: (-n[p], p))
    for _, t in r.operator_texts():
        r.hints += [p for p in PATH_RX.findall(t) if p in pset and p not in r.hints]
    return r


def roles_of(view: TaskView) -> Roles:
    """The task's roles, rebuilt when its file set changes (a write or a refreshed manifest)."""
    key = tuple(sorted(view.files.items()))
    with _lock:
        cached = getattr(view, "_roles", None)
        if cached is not None and cached[0] == key:
            return cached[1]
    r = build(view)
    with _lock:
        view._roles = (key, r)
    return r
