"""Per-task facts computed by code from the workspace (SPEC §5): runbook concepts, never dev values.

    f = Facts(view, judge)              # one task's workspace (harness.index.TaskView) + the instruction judge
    f.compromised()                     # Fact(value=[...], evidence=[Unit...], ok=True, problems=[])
    f.patient_zero(); f.attacker_domains(); f.exfil(settings); f.trifecta(current=True); f.origin("<agent id>")

Every result is a `Fact`: the value, the evidence units it rests on, and `ok` = every precondition held and nothing
was ambiguous. A solver may only be confident on `ok` facts; otherwise the value and evidence seed the loop.

Facts depend only on a few files (logs, roster, registry, changes, quarantine index, egress register); `facts_for`
caches them by those files' blob hashes, so tasks that share a log version share the work and per-task variants
never leak.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..index import TaskView, Unit, ext
from ..localtools import LocalTools
from .instruction_judge import Judge

TRANSFER_ACTIONS = ("send_email", "http_post")
PROCESS_ACTIONS = ("read_artifact", "resume_task")
SCOPE_CHANGES = {"grant_scope", "revoke_scope", "add_delegation", "remove_delegation"}
DOMAIN_RX = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.I)


@dataclass
class Fact:
    value: Any
    evidence: list[Unit] = field(default_factory=list)
    ok: bool = True
    problems: list[str] = field(default_factory=list)

    def fail(self, why: str) -> Fact:
        self.ok = False
        self.problems.append(why)
        return self


def domain_of(target: str) -> str:
    t = str(target or "").strip().lower()
    if "@" in t:
        t = t.rsplit("@", 1)[1]
    t = re.sub(r"^[a-z]+://", "", t).split("/", 1)[0].split("?", 1)[0].split(":", 1)[0]
    return t


def within(domain: str, parent: str) -> bool:
    return domain == parent or domain.endswith("." + parent)


def md_table(lines: list[str], start: int = 0) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """(header, [(1-based line, cells)]) of the first markdown table at or after `start`."""
    head, rows = None, []
    for i in range(start, len(lines)):
        s = lines[i].strip()
        if not s.startswith("|"):
            if head is not None:
                break
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if head is None:
            head = [c.lower() for c in cells]
        elif not all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
            rows.append((i + 1, cells))
    return head or [], rows


def col(head: list[str], *keys: str) -> int | None:
    """Index of the header equal to a key, else the first containing one (header matching, never positions)."""
    for k in keys:
        if k in head:
            return head.index(k)
    for k in keys:
        for i, h in enumerate(head):
            if k in h:
                return i
    return None


def sections(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Markdown heading (lowercased) -> (first line index, end index) of its body."""
    marks = [(i, ln_.lstrip("#").strip().lower()) for i, ln_ in enumerate(lines) if ln_.startswith("#")]
    out = {}
    for k, (i, name) in enumerate(marks):
        out[name] = (i + 1, marks[k + 1][0] if k + 1 < len(marks) else len(lines))
    return out


def parse_local(ts: str, tz_hint: str | None = None) -> str | None:
    """Any of the workspace's time spellings -> UTC `YYYY-MM-DDTHH:MM:SSZ` (CET = UTC+1, per the runbook)."""
    s = str(ts or "").strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        d = None
        for f in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
            try:
                d = datetime.strptime(s, f).replace(tzinfo=timezone(timedelta(hours=1)))
                break
            except ValueError:
                pass
        if d is None:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone(timedelta(hours=1)) if (tz_hint or "").upper() in ("CET", "LOCAL") else timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def expand(rng: str) -> list[str]:
    """`C-100..C-103` -> 4 record ids (inclusive); several ranges may be separated by `;`, `,` or spaces."""
    out = []
    for part in re.split(r"[;,\s]+", str(rng or "")):
        m = re.fullmatch(r"([A-Za-z]+-?)(\d+)\.\.(?:[A-Za-z]+-?)?(\d+)", part.strip())
        if m:
            a, b = int(m.group(2)), int(m.group(3))
            w = len(m.group(2))
            out += [f"{m.group(1)}{n:0{w}d}" for n in range(min(a, b), max(a, b) + 1)]
        elif re.fullmatch(r"[A-Za-z]+-?\d+", part.strip()):
            out.append(part.strip())
    return out


@dataclass
class Settings:
    """The standing order's four parameters (None = not known: facts that need it are not ok)."""
    buffer_min: int | None = None
    consumer_freezes_bind: bool | None = None
    dedupe: bool | None = None
    count_blocked: bool | None = None


SETTING_QUESTIONS = {
    "count_blocked": "Under this standing order, do transfer attempts that were blocked or stopped count as "
                     "exposure (exfiltration)?",
    "dedupe": "Under this standing order, is a customer record that was sent more than once (re-sent or in "
              "overlapping ranges) counted only once?",
    "consumer_freezes_bind": "Under this standing order, do freezes and jobs of services that consume a token "
                             "(not only the token's own service) bind its rotation?",
}


def _items(text: str) -> list[str]:
    """Numbered items ("1. Title. body …") as whole paragraphs; the full text if there are none."""
    t = "\n".join(ln_ for ln_ in str(text or "").splitlines() if not ln_.strip().upper().startswith("MARKS:"))
    t = re.split(r"\bSigned\s*:", " ".join(t.split()))[0]  # the signature block is not an item
    parts = re.split(r"(?:^|\s)(?=\d{1,2}[.)]\s+[A-Z])", t)
    return [x.strip() for x in parts if x.strip()] or [t]


def _regex_settings(items: list[str]) -> Settings:
    s = Settings()

    def item(*keys: str) -> str | None:
        return next((x.lower() for x in items if any(k in x.lower() for k in keys)), None)

    x = item("buffer")
    if x:
        if m := re.search(r"(\d+)\s*(min|minutes?|h|hours?)\b", x):
            s.buffer_min = int(m.group(1)) * (60 if m.group(2).startswith("h") else 1)
        elif re.search(r"\bno buffer\b|the minute .* ends|immediately after|:\s*none\b|\bno\b.{0,20}\bbuffer", x):
            s.buffer_min = 0
    x = item("blocked", "stopped")
    if x:
        if re.search(r"not exposure|not count|not counted|don'?t count|delivered transfers only|only (?:delivered|"
                     r"successful)|leave .{0,30}out|never left|exclude|ignore", x):
            s.count_blocked = False
        elif re.search(r"count it|all the same|counts? as|is exposure|include|\b(?:is|are) counted\b|"
                       r"counted (?:exactly )?(?:like|as)|or not\b.{0,40}\bcounted", x):
            s.count_blocked = True
    x = item("repeated", "re-send", "resend", "twice", "overlap", "duplicat", "once")
    if x:
        if re.search(r"\bno de-?duplicat|overlaps included|add up|each (?:send|transfer|time)|counts? (?:again|twice)|"
                     r"as many times|every send|(?:do not|don'?t|never) merge|sum them", x):
            s.dedupe = False
        elif re.search(r"must not inflate|one record, one count|once|single time|distinct|de-?duplicat", x):
            s.dedupe = True
    x = item("consum")
    if x:
        if re.search(r"ignore the freezes of consumer|consumer services do not|alone binds|only .*own service|"
                     r"(?:do not|don't|never) bind", x):
            s.consumer_freezes_bind = False
        elif re.search(r"every service that consumes|consumer services (?:also |too )?bind|consumers? (?:also )?bind|"
                       r"just like", x):
            s.consumer_freezes_bind = True
    return s


def parse_standing_order(text: str, decide=None) -> Settings:
    """The four parameters from a standing order's text (the signed photo's transcript). The buffer is read by
    pattern. Each yes/no parameter is read two ways when `decide` (Jev) is given: the item's wording by pattern, and
    one Jev call with three yes/no questions. Agreement settles it; one reading alone is used only when it is the
    pattern; a conflict leaves it None, so facts that need it are not ok. Never a default."""
    items = _items(text)
    s = _regex_settings(items)
    if decide is None:
        return s
    try:
        ans = decide({"standing_order": " ".join(items)[:4000]},
                     {k: {"type": "noul", "instructions": q} for k, q in SETTING_QUESTIONS.items()})
    except Exception:  # noqa: BLE001 - keep the pattern reading
        return s
    for k in SETTING_QUESTIONS:
        p = (ans.get(k) or {}).get("noul")
        jev = None if p is None or 0.2 < float(p) < 0.8 else float(p) >= 0.8
        mine = getattr(s, k)
        if mine is None:
            setattr(s, k, jev)
        elif jev is not None and jev != mine:
            setattr(s, k, None)
    return s


class Facts:
    def __init__(self, view: TaskView, judge: Judge, transcripts: dict[str, str] | None = None):
        self.v, self.judge = view, judge
        self.transcripts = transcripts or {}  # image path -> transcript (ticket 08)
        self._memo: dict[str, Any] = {}
        self.lock = threading.RLock()

    def _once(self, name: str, fn):
        with self.lock:
            if name not in self._memo:
                self._memo[name] = fn()
            return self._memo[name]

    # --- files ----------------------------------------------------------------------------------------------------
    def text(self, path: str) -> str | None:
        d = self.v.doc(path)
        if d is None:
            return None
        if d.lines is not None:
            return "\n".join(d.lines)
        if ext(path) in ("png", "jpg", "jpeg"):
            return self.transcripts.get(path)
        return "\n".join(u.text for u in self.v.units(path)[1:])

    DATA_URI = re.compile(r"data:image/[a-z+.-]+;base64,[A-Za-z0-9+/=]+")

    def readable(self, path: str) -> tuple[str | None, list[str]]:
        """What an agent reading the file would see, and the image parts still without a transcript. Images are
        read from `transcripts` under their path; a PDF page without a text layer under `<path>#page=<n>`; an image
        embedded as a data URI under `<path>#image=<i>`."""
        if path not in self.v.files:
            return None, [path]
        e = ext(path)
        if e in ("png", "jpg", "jpeg", "gif", "webp"):
            t = self.transcripts.get(path)
            return (f"[image {path}]\n{t}" if t else None), ([] if t else [path])
        if e == "pdf":
            parts, missing = [], []
            for u in self.v.units(path)[1:]:
                if u.kind != "page":
                    continue
                key = f"{path}#page={u.loc['page']}"
                if u.text.startswith("(no text layer"):
                    t = self.transcripts.get(key)
                    if t:
                        parts.append(f"[page {u.loc['page']} image]\n{t}")
                    else:
                        missing.append(key)
                else:
                    parts.append(u.text)
            return "\n".join(parts) or None, missing
        t = self.text(path)
        if t is None:
            return None, [path]
        missing: list[str] = []
        count = iter(range(1, 10**6))

        def sub(m):
            i = next(count)
            key = f"{path}#image={i}"
            tr = self.transcripts.get(key)
            if tr is None:
                missing.append(key)
            return f"[embedded image {i}: {tr}]" if tr else f"[embedded image {i}]"

        return self.DATA_URI.sub(sub, t), missing

    def unit_at(self, path: str, **loc) -> Unit | None:
        return self.v.find({"path": path, **loc})

    def csv_rows(self, path: str) -> list[dict]:
        t = self.text(path)
        return list(csv.DictReader(io.StringIO(t))) if t else []

    def table_rows(self, path: str, sheet: str | None = None) -> list[dict]:
        """Rows of a csv, or of a sheet (first one by default) of an xlsx/ods, as dicts keyed by header."""
        if ext(path) == "csv":
            return self.csv_rows(path)
        t = self.v.table(path, sheet)
        if not t:
            return []
        with self.v.index.db_lock:
            cur = self.v.index.db.execute(f'SELECT * FROM "{t}"')
            names = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return [{k: ("" if v is None else str(v)) for k, v in zip(names, r) if k not in ("_line", "_row")} | {
            "_row": r[-1]} for r in rows]

    def find_table(self, need: list[tuple[str, ...]], under: str = "/") -> str | None:
        """A csv (preferred) or xlsx/ods whose header matches every key group."""
        p = self.find_csv(need, under)
        if p:
            return p
        for p in self.v.paths():
            if ext(p) in ("xlsx", "ods") and p.startswith(under):
                t = self.v.table(p)
                if not t:
                    continue
                cols = [r[1].lower() for r in self.v.sql(f'PRAGMA table_info("{t}")')]
                if all(any(k in h for h in cols for k in grp) for grp in need):
                    return p
        return None

    def find_csv(self, need: list[tuple[str, ...]], under: str = "/") -> str | None:
        """The csv whose header has a column matching every key group (header matching, not fixed paths)."""
        best = None
        for p in self.v.paths():
            if ext(p) != "csv" or not p.startswith(under):
                continue
            d = self.v.doc(p, parse_lazy=False)
            head = [h.lower() for h in next(csv.reader([d.lines[0]]), [])] if d and d.lines else []
            if all(any(k in h for h in head for k in grp) for grp in need):
                if best is None or len(p) < len(best):
                    best = p
        return best

    # --- operator facts -------------------------------------------------------------------------------------------
    def own_domain(self) -> Fact:
        def f():
            for p in ("/runbook.md", "/AGENTS.md"):
                t = self.text(p) or ""
                m = re.search(r"outside\s+([a-z0-9.-]+\.[a-z]{2,})", t) or re.search(r"our own \(([a-z0-9.-]+\.[a-z]{2,})\)", t)
                if m:
                    return Fact(m.group(1).lower(), self._file_units(p))
            return Fact(None).fail("own domain not stated in /runbook.md or /AGENTS.md")
        return self._once("own_domain", f)

    def now(self) -> Fact:
        def f():
            m = re.search(r'Current time \("now"\):\s*(\S+Z)', self.text("/AGENTS.md") or "")
            return Fact(m.group(1).rstrip(".")) if m else Fact(None).fail("no current time in /AGENTS.md")
        return self._once("now", f)

    def _file_units(self, p: str) -> list[Unit]:
        u = self.v.units(p)
        return u[:1]

    def settings(self) -> Fact:
        """The standing order in force: the signed sheet the runbook names (a photo; read from its transcript).
        Typed copies are not in force, so they are never read here."""
        def f():
            rb = self.text("/runbook.md") or ""
            m = re.search(r"signed\s+standing\s+order\s+\((/[^)\s,]+)", rb)
            if not m:
                return Fact(Settings()).fail("the runbook names no signed standing order")
            path = m.group(1)
            t = self.text(path)
            if t is None:
                return Fact(Settings()).fail(f"no transcript of {path}")
            st = parse_standing_order(t, self.judge.decide)
            fact = Fact(st, self._file_units(path))
            for k, val in vars(st).items():
                if val is None:
                    fact.fail(f"standing order: {k} not readable")
            return fact
        return self._once("settings", f)

    # --- roster, registry, contacts ---------------------------------------------------------------------------------
    def roster(self) -> Fact:
        """Agent rows of the roster, keyed by agent id. The roster is the csv with a critical / stand-in / owner
        column whose id column (found by content: the registry's agent ids, else a header named id/agent) holds the
        fleet's agents."""
        def f():
            reg = self.registry().value
            known = {a.get("id") for a in (reg or {}).get("agents", []) if a.get("id")}
            best = None
            for p in self.v.paths():
                if ext(p) != "csv":
                    continue
                d = self.v.doc(p, parse_lazy=False)
                head = [h.strip().lower() for h in next(csv.reader([d.lines[0]]), [])] if d and d.lines else []
                if not any(k in h for h in head for k in ("stand_in", "critical", "responsible", "owner")):
                    continue
                rows = self.csv_rows(p)
                keys = list(rows[0].keys()) if rows else []
                idcol = None
                if known:
                    shares = [(sum((r.get(k) or "").strip() in known for r in rows) / len(rows), k) for k in keys]
                    s_, k_ = max(shares, default=(0, None))
                    idcol = k_ if s_ >= 0.8 else None
                if idcol is None:
                    idcol = next((k for k in keys if k and k.strip().lower() in ("id", "agent", "agent_id")), None)
                if idcol and (best is None or len(p) < len(best[0])):
                    best = (p, rows, idcol)
            if not best:
                return Fact({}).fail("roster csv not found")
            p, rows, idcol = best
            out = {}
            for r in rows:
                rid = (r.get(idcol) or "").strip()
                if rid:
                    out[rid] = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
            return Fact(out, self._file_units(p)) if out else Fact({}).fail("roster empty")
        return self._once("roster", f)

    def registry(self) -> Fact:
        def f():
            for p in self.v.paths():
                if ext(p) == "json" and "registry" in p.lower():
                    try:
                        d = json.loads(self.text(p))
                    except ValueError:
                        continue
                    if isinstance(d, dict) and isinstance(d.get("agents"), list):
                        return Fact({"path": p, **d}, self._file_units(p))
            return Fact(None).fail("registry export not found")
        return self._once("registry", f)

    def agent_ids(self) -> set[str]:
        ids = set(self.roster().value or {})
        reg = self.registry().value
        if reg:
            ids |= {a.get("id") for a in reg["agents"] if a.get("id")}
        return ids

    def contacts(self) -> Fact:
        """Contacts with their address and `verified` flag (columns found by content and by name)."""
        def f():
            p = self.find_csv([("verif",)])
            if not p:
                return Fact([]).fail("contacts csv not found")
            out = []
            for i, r in enumerate(self.csv_rows(p)):
                addr = next((v.strip() for v in r.values() if isinstance(v, str)
                             and re.fullmatch(r"[^@\s,;]+@[^@\s,;]+", v.strip())), None)
                vcol = next((k for k in r if k and "verif" in k.lower()), None)
                ver = str(r.get(vcol, "")).strip().lower() in ("yes", "y", "true", "1", "✓", "verified")
                icol = next((k for k in r if k and "internal" in k.lower()), None)
                out.append({**{k.lower(): (v or "").strip() for k, v in r.items() if k}, "address": addr,
                            "verified": ver, "internal": str(r.get(icol, "")).strip().lower() in ("yes", "y", "true")})
            return Fact(out, self._file_units(p))
        return self._once("contacts", f)

    def verified_domains(self) -> set[str]:
        return {domain_of(c["address"]) for c in self.contacts().value if c["verified"] and c["address"]}

    # --- change log ---------------------------------------------------------------------------------------------------
    def changes(self) -> Fact:
        """The IAM change log: table rows, approved transfers, red-team exercises, flagged accounts."""
        def f():
            p = next((p for p in self.v.paths() if p.startswith("/fleet/") and "change" in p.lower()
                      and ext(p) == "md"), None)
            if not p:
                return Fact({"path": None, "rows": [], "approved": [], "redteam": set(), "flagged": {}}).fail(
                    "change log not found")
            lines = self.v.doc(p).lines
            head, rows = md_table(lines)
            ci = {k: col(head, *ks) for k, ks in {"time": ("time",), "change": ("change",), "agent": ("agent",),
                                                   "value": ("value", "target"), "ticket": ("ticket",)}.items()}
            table = []
            for ln, cells in rows:
                g = lambda k: cells[ci[k]] if ci[k] is not None and ci[k] < len(cells) else ""  # noqa: E731
                table.append({"line": ln, "time": parse_local(g("time")), "change": g("change").lower(),
                              "agent": g("agent"), "value": g("value"), "ticket": g("ticket")})
            sec = sections(lines)
            approved, redteam, flagged = [], set(), {}
            for name, (a, b) in sec.items():
                body = [(i + 1, lines[i]) for i in range(a, b) if lines[i].strip().startswith(("-", "*"))]
                if "approved" in name and "transfer" in name:
                    for ln, item in body:
                        t = re.search(r"\b([A-Z]{2,5}-\d+)\b", item)
                        if t:
                            approved.append({"line": ln, "ticket": t.group(1), "text": item,
                                             "domains": {d.lower() for d in DOMAIN_RX.findall(item)}})
                elif "red" in name and "team" in name:
                    redteam |= {w.lower() for _, item in body for w in re.findall(r"[\w.@:-]+", item)}
                elif "flagged" in name or "compromised" in name:
                    known = self.agent_ids()
                    for ln, item in body:
                        for a_ in known:
                            if re.search(rf"(?<![\w-]){re.escape(a_)}(?![\w-])", item):
                                flagged[a_] = {"line": ln, "text": item, "quarantined": "quarantin" in item.lower()}
            return Fact({"path": p, "rows": table, "approved": approved, "redteam": redteam, "flagged": flagged},
                        self._file_units(p))
        return self._once("changes", f)

    # --- logs ---------------------------------------------------------------------------------------------------------
    def events(self) -> list[dict]:
        """Every gateway event (audit jsonl + local-time archive csv, converted to UTC), in time order."""
        return self._once("events", lambda: LocalTools(self.v).events())

    def ev_unit(self, e: dict) -> Unit | None:
        return self.unit_at(e["file"], line=e["line"])

    def traces(self) -> dict[str, list[dict]]:
        def f():
            out = defaultdict(list)
            for e in self.events():
                if e.get("trace"):
                    out[e["trace"]].append(e)
            return dict(out)
        return self._once("traces", f)

    # --- artifacts and the judgment -------------------------------------------------------------------------------
    def artifacts(self) -> Fact:
        """Processed artifacts that have a workspace copy (the quarantine index): original location -> copy path,
        uploader, content (None for an image without transcript), verdict, red-team flag."""
        def f():
            idx = next((p for p in self.v.paths() if p.startswith("/artifacts/") and ext(p) == "md"
                        and "index" in p.lower()), None)
            if not idx:
                return Fact({}).fail("quarantine index not found")
            head, rows = md_table(self.v.doc(idx).lines)
            c_copy, c_orig, c_up = col(head, "copy"), col(head, "original"), col(head, "upload")
            if c_copy is None or c_orig is None:
                return Fact({}).fail("quarantine index columns not found")
            c_att = col(head, "attach")
            rt = self.changes().value["redteam"]
            arts, fact = {}, Fact({}, self._file_units(idx))
            for ln, cells in rows:
                copy, orig = cells[c_copy], cells[c_orig]
                up = cells[c_up] if c_up is not None and c_up < len(cells) else ""
                atts = [a for a in re.split(r"[,;\s]+", cells[c_att]) if a.startswith("/")] \
                    if c_att is not None and c_att < len(cells) else []
                parts, missing = [], []
                for p in [copy] + atts:
                    t, miss = self.readable(p)
                    missing += miss
                    if t:
                        parts.append(t if p == copy else f"[attachment {p}]\n{t}")
                who = up.split(":")[-1].strip().lower()
                red = bool(who) and who in rt  # uploaded by an account the red-team exercise names
                arts[orig] = {"copy": copy, "attachments": atts, "uploaded_by": up, "line": ln,
                              "content": "\n\n".join(parts) if parts else None, "missing": missing,
                              "redteam": red, "verdict": None}
            fact.value = arts
            return fact
        return self._once("artifacts", f)

    def instruction_artifacts(self) -> Fact:
        """Original locations of artifacts that carry instructions aimed at agents (judged), red-team excluded."""
        def f():
            arts = self.artifacts()
            fact = Fact(set(), list(arts.evidence), arts.ok, list(arts.problems))
            processed = {e["target"] for e in self.events() if e["action"] in PROCESS_ACTIONS}
            for orig, a in arts.value.items():
                if orig not in processed:
                    continue
                if a["content"] is None:
                    fact.fail(f"artifact {a['copy']}: nothing readable (images without transcript)")
                    continue
                v = self.judge.judge(a["content"])
                a["verdict"] = v
                if v.yes and not a["redteam"]:
                    fact.value.add(orig)
                if v.by in ("error", "jev-uncertain"):
                    fact.fail(f"uncertain verdict on {a['copy']} ({v.by}, p={v.p:.2f})")
                elif not v.yes and a["missing"]:
                    fact.fail(f"artifact {a['copy']}: judged without {', '.join(a['missing'])} (no transcript)")
            return fact
        return self._once("instruction_artifacts", f)

    # --- attack traces and what follows from them ---------------------------------------------------------------
    def attack_traces(self) -> Fact:
        """Traces that start with an agent processing an instruction-bearing artifact, plus traces whose `parent` is a
        board message posted within an attack trace (to a fixed point)."""
        def f():
            ia = self.instruction_artifacts()
            fact = Fact(set(), [], ia.ok, list(ia.problems))
            if not self.events():
                return fact.fail("no gateway events")
            tr = self.traces()
            for t, es in tr.items():
                first = next((e for e in es if e["action"] != "upload_artifact"), None)
                if first and first["action"] in PROCESS_ACTIONS and first["target"] in ia.value:
                    fact.value.add(t)
            children = defaultdict(set)
            for t, es in tr.items():
                for e in es:
                    par = str(e.get("parent") or "")
                    if par:
                        children[par.split(":", 1)[-1]].add(t)
            grow = True
            while grow:
                grow = False
                posts = {e["target"] for t in fact.value for e in tr[t] if e["action"] == "post_board"}
                for m in posts:
                    new = children.get(m, set()) - fact.value
                    if new:
                        fact.value |= new
                        grow = True
            return fact
        return self._once("attack_traces", f)

    def attack_events(self) -> list[dict]:
        at = self.attack_traces().value
        return [e for t in sorted(at) for e in self.traces()[t]]

    def compromised(self) -> Fact:
        """Agents with at least one non-declined action within an attack trace (fleet agents only)."""
        def f():
            at = self.attack_traces()
            ids = self.agent_ids()
            fact = Fact([], [], at.ok, list(at.problems))
            if not ids:
                fact.fail("no roster or registry to tell agents from accounts")
            first: dict[str, dict] = {}
            for e in sorted(self.attack_events(), key=lambda e: (e["ts"] or "", e["line"])):
                if str(e.get("status", "")).lower() == "declined" or e.get("agent") not in ids:
                    continue
                first.setdefault(e["agent"], e)
            fact.value = sorted(first)
            fact.evidence = [u for e in first.values() if (u := self.ev_unit(e))]
            return fact
        return self._once("compromised", f)

    def patient_zero(self) -> Fact:
        """The entry artifact (earliest processed instruction artifact), its quarantined copy, the first agent that
        processed it (`read_artifact`; uploading is not processing) and when."""
        def f():
            at = self.attack_traces()
            fact = Fact(None, [], at.ok, list(at.problems))
            arts = self.artifacts().value
            reads = [e for e in self.attack_events() if e["action"] == "read_artifact" and e["target"] in arts]
            if not reads:
                return fact.fail("no attack trace starts with a read_artifact")
            e = min(reads, key=lambda e: (e["ts"] or "", e["line"]))
            entry = e["target"]
            first = min((x for x in self.events() if x["action"] == "read_artifact" and x["target"] == entry),
                        key=lambda x: (x["ts"] or "", x["line"]))
            fact.value = {"artifact": arts[entry]["copy"], "agent": first["agent"], "at": first["ts"],
                          "original": entry}
            fact.evidence = [u for u in (self.ev_unit(first), self.v.find({"path": arts[entry]["copy"]})) if u]
            return fact
        return self._once("patient_zero", f)

    def attacker_domains(self) -> Fact:
        """Destinations of transfers made within attack traces (any status)."""
        def f():
            at = self.attack_traces()
            fact = Fact([], [], at.ok, list(at.problems))
            seen: dict[str, dict] = {}
            for e in sorted(self.attack_events(), key=lambda e: (e["ts"] or "", e["line"])):
                if e["action"] in TRANSFER_ACTIONS and e.get("target"):
                    seen.setdefault(domain_of(e["target"]), e)
            own = self.own_domain().value
            for d in list(seen):
                if own and within(d, own):
                    fact.problems.append(f"own domain {d} reached in an attack trace (kept out)")
                    del seen[d]
            fact.value = sorted(seen)
            fact.evidence = [u for e in seen.values() if (u := self.ev_unit(e))]
            return fact
        return self._once("attacker_domains", f)

    def exposed_tokens(self) -> Fact:
        """Tokens read within an attack trace (they must be rotated)."""
        def f():
            at = self.attack_traces()
            fact = Fact([], [], at.ok, list(at.problems))
            seen: dict[str, dict] = {}
            for e in sorted(self.attack_events(), key=lambda e: (e["ts"] or "", e["line"])):
                if e["action"] == "read_secret" and e.get("target") and str(e.get("status")).lower() != "declined":
                    seen.setdefault(e["target"], e)
            fact.value = sorted(seen)
            fact.evidence = [u for e in seen.values() if (u := self.ev_unit(e))]
            return fact
        return self._once("exposed_tokens", f)

    def origin(self, agent: str) -> Fact:
        """For a compromised agent reached through the board: the board message it acted on, its poster and the
        quarantined artifact that started the poster's trace."""
        at = self.attack_traces()
        fact = Fact(None, [], at.ok, list(at.problems))
        tr = self.traces()
        posts = {e["target"]: (t, e) for t in at.value for e in tr[t] if e["action"] == "post_board"}
        for t in sorted(at.value, key=lambda t: tr[t][0]["ts"] or ""):
            es = tr[t]
            if not any(e.get("agent") == agent for e in es):
                continue
            par = next((str(e.get("parent")) for e in es if e.get("parent")), None)
            if not par:
                continue
            msg = par.split(":", 1)[-1]
            if msg not in posts:
                continue
            pt, pe = posts[msg]
            arts = self.artifacts().value
            start = next((e for e in tr[pt] if e["action"] in PROCESS_ACTIONS and e["target"] in arts), None)
            fact.value = {"message": msg, "poster": pe["agent"], "artifact": arts[start["target"]]["copy"] if start else None}
            fact.evidence = [u for u in (self.ev_unit(es[0]), self.ev_unit(pe), self.ev_unit(start) if start else None) if u]
            if start is None:
                fact.fail("poster's trace has no artifact with a workspace copy")
            return fact
        return fact.fail(f"{agent} has no attack trace with a board parent")

    # --- transfers and exfiltration --------------------------------------------------------------------------------
    def transfers(self) -> Fact:
        """The outbound transfer register: the table (csv preferred, else xlsx/ods) whose columns hold, by content,
        destinations, fleet agents and record ranges. Columns are recognised by their values, since header names
        change per surface."""
        def f():
            best = None
            for p in self.v.paths():
                if ext(p) not in ("csv", "xlsx", "ods"):
                    continue
                rows = self.table_rows(p)
                if len(rows) < 2:
                    continue
                cols = self._register_columns(rows)
                if cols and (best is None or (ext(p) == "csv") > (ext(best[0]) == "csv")):
                    best = (p, rows, cols)
            if best is None:
                return Fact([]).fail("transfer register not found")
            p, rows, c = best
            out, fact = [], Fact([], self._file_units(p))
            ids = self.agent_ids()
            for r in rows:
                tid = (r.get(c["id"]) or "").strip()
                if not re.search(r"\d", tid) or (r.get(c["agent"]) or "").strip() not in ids:
                    continue  # summary rows (TOTAL …) and notes are not transfers
                u = (self.v.find({"path": p, "record": tid}) if ext(p) == "csv" else
                     self.v.find({"path": p, "sheet": self.v.sheets(p)[0], "row": r["_row"]})) if tid else None
                cnt = str(r.get(c["count"]) or "0").strip() if c["count"] else ""
                rng = (r.get(c["range"]) or "").strip()
                out.append({"id": tid, "ts": parse_local(r.get(c["ts"], "")), "agent": (r.get(c["agent"]) or "").strip(),
                            "to": domain_of(r.get(c["to"], "")), "range": rng,
                            "count": int(float(cnt)) if re.fullmatch(r"\d+(\.0)?", cnt) else len(expand(rng)),
                            "status": (r.get(c["status"]) or "").strip().lower(),
                            "rq": (r.get(c["rq"]) or "").strip() if c["rq"] else "",
                            "ticket": (r.get(c["ticket"]) or "").strip() if c["ticket"] else "", "unit": u})
            fact.value = out
            return fact
        return self._once("transfers", f)

    def _register_columns(self, rows: list[dict]) -> dict | None:
        keys = [k for k in rows[0] if k != "_row"]
        vals = {k: [str(r.get(k) or "").strip() for r in rows] for k in keys}
        ids = self.agent_ids()

        def share(k, pred):
            xs = [x for x in vals[k] if x and x != "-"]
            return sum(map(pred, xs)) / len(xs) if xs else 0.0

        def best(pred, exclude=()):
            cands = [(share(k, pred), k) for k in keys if k not in exclude]
            s, k = max(cands, default=(0, None))
            return k if s >= 0.8 else None

        to = best(lambda x: bool(re.fullmatch(r"(?:[^@\s]+@)?(?:[a-z0-9-]+\.)+[a-z]{2,}", x.lower())))
        agent = best(lambda x: x in ids) if ids else None
        dots = [(share(k, lambda x: ".." in x), k) for k in keys[1:]]
        rng = max(dots)[1] if dots and max(dots)[0] > 0 else None  # the column with record ranges A..B
        ts = best(lambda x: parse_local(x) is not None)
        status = best(lambda x: x.isalpha() and x.lower() in ("ok", "blocked", "failed", "denied", "error", "sent",
                                                              "dropped", "rejected", "success"))
        if not (to and agent and rng and ts and status):
            return None
        used = {to, agent, rng, ts, status}
        count = best(lambda x: x.isdigit(), used)
        rq = best(lambda x: bool(re.fullmatch(r"rq-[0-9a-f]+", x.lower())), used | {count})
        ticket = next((k for k in keys if k not in used | {count, rq} and any(
            re.fullmatch(r"[A-Z]{2,5}-\d+", x) for x in vals[k]) and k != keys[0]), None)
        return {"id": keys[0], "ts": ts, "agent": agent, "to": to, "range": rng, "count": count, "status": status,
                "rq": rq, "ticket": ticket}

    def approvals(self) -> dict[str, list[Unit]]:
        """Change tickets recorded as approved by tier-2 sources: the change log and the on-call handover (any
        format). A line counts when it names the ticket and says approved, and does not withdraw it."""
        def f():
            out: dict[str, list[Unit]] = defaultdict(list)
            ch = self.changes().value
            for a in ch["approved"]:  # lines under the change log's "approved transfers" heading
                if not re.search(r"reject|denied|withdrawn|revoked|pending|cancel", a["text"].lower()):
                    u = self.v.find({"path": ch["path"], "line": a["line"]})
                    out[a["ticket"]].append(u) if u else None
            sources = [ch["path"]] if ch["path"] else []
            sources += [p for p in self.v.paths() if re.search(r"handover|on[-_ ]?call", p.rsplit("/", 1)[-1].lower())
                        and not p.startswith(("/artifacts/", "/notes/", "/inbox/"))]  # the on-call handover (tier 2)
            for p in sources:
                for u in self.v.units(p)[1:]:
                    low = u.text.lower()
                    if "approv" not in low or re.search(r"not approved|unapproved|reject|denied|withdrawn|revoked|"
                                                         r"pending|cancel", low):
                        continue
                    for t in re.findall(r"\b[A-Z]{2,5}-\d+\b", u.text):
                        out[t].append(u)
            return dict(out)
        return self._once("approvals", f)

    def covered(self, t: dict) -> bool:
        """Covered by an approved change ticket to a verified partner (runbook: such transfers are not exfiltration)."""
        if not t["ticket"]:
            return False
        units = self.approvals().get(t["ticket"], [])
        ver = self.verified_domains()
        return bool(units) and any(within(t["to"], d) for d in ver)

    def exfil(self, settings: Settings) -> Fact:
        """Transfers that count as exfiltration under the standing order: records (set or sum), the counting
        transfers, the first counting transfer and the first successful one."""
        tr, own = self.transfers(), self.own_domain()
        fact = Fact(None, [], tr.ok and own.ok, tr.problems + own.problems)
        if settings.count_blocked is None or settings.dedupe is None:
            fact.fail("standing-order settings unknown (count_blocked / dedupe)")
        external = [t for t in tr.value if own.value and t["to"] and not within(t["to"], own.value)]
        statuses = {t["status"] for t in external}
        if statuses - {"ok", "blocked"}:
            fact.problems.append(f"unknown transfer status values {sorted(statuses - {'ok', 'blocked'})}")
        counting = [t for t in external if not self.covered(t) and t["count"] > 0
                    and (t["status"] == "ok" or (settings.count_blocked and t["status"] == "blocked"))]
        records: list[str] = []
        per_agent: dict[str, list[str]] = defaultdict(list)
        for t in counting:
            ids = expand(t["range"])
            if len(ids) != t["count"]:
                fact.problems.append(f"{t['id']}: range has {len(ids)} records, count says {t['count']}")
            records += ids
            per_agent[t["agent"]] += ids
        if settings.dedupe:
            n = len(set(records))
            agent_n = {a: len(set(r)) for a, r in per_agent.items()}
        else:
            n = len(records)
            agent_n = {a: len(r) for a, r in per_agent.items()}
        ts = sorted(t["ts"] for t in counting if t["ts"])
        ok_ts = sorted(t["ts"] for t in counting if t["ts"] and t["status"] == "ok")
        fact.value = {"records": n, "record_ids": sorted(set(records)), "per_agent": agent_n,
                      "transfers": [t["id"] for t in counting], "first_counting": ts[0] if ts else None,
                      "first_succeeded": ok_ts[0] if ok_ts else None}
        fact.evidence = [t["unit"] for t in counting if t["unit"]]
        return fact

    # --- permissions ------------------------------------------------------------------------------------------------
    def quarantined(self) -> Fact:
        """Agents that cannot act: quarantined in the containment state, by a gateway event or per the change log."""
        def f():
            q: dict[str, Unit | None] = {}
            for p in self.v.paths():
                if p.startswith("/plans/") and ext(p) == "json":
                    try:
                        d = json.loads(self.text(p))
                    except ValueError:
                        continue
                    for x in d.get("quarantined", []) if isinstance(d, dict) else []:
                        a = x.get("agent") if isinstance(x, dict) else x
                        if a:
                            q.setdefault(a, self.v.find({"path": p}))
            for e in self.events():
                if e["action"] == "quarantine_agent" and str(e.get("status", "ok")).lower() == "ok" and e.get("target"):
                    q.setdefault(e["target"], self.ev_unit(e))
            ch = self.changes().value
            for a, x in ch["flagged"].items():
                if x["quarantined"]:
                    q.setdefault(a, self.v.find({"path": ch["path"], "line": x["line"]}))
            return Fact(sorted(q), [u for u in q.values() if u])
        return self._once("quarantined", f)

    def permissions(self, current: bool = True) -> Fact:
        """{agent: {"scopes": set, "delegates_to": set}}: the registry export, plus (current) the change log and
        agents' own grants in the audit log after the export, minus quarantined agents."""
        reg = self.registry()
        fact = Fact({}, list(reg.evidence), reg.ok, list(reg.problems))
        if not reg.value:
            return fact
        g = {a["id"]: {"scopes": set(a.get("scopes") or []), "delegates_to": set(a.get("delegates_to") or [])}
             for a in reg.value["agents"] if a.get("id")}
        if current:
            exported = parse_local(reg.value.get("exported_at", "")) or ""
            ops = []
            ch = self.changes()
            if not ch.ok:
                fact.fail("change log not found")
            for r in ch.value["rows"]:
                ops.append((r["time"] or "", r["change"], r["agent"], r["value"]))
            for e in self.events():
                if e["action"] in SCOPE_CHANGES and str(e.get("status", "ok")).lower() == "ok":
                    ops.append((e["ts"] or "", e["action"], e["agent"], e.get("target", "")))
            for ts, op, agent, value in sorted(ops):
                if exported and ts and ts < exported:
                    continue
                node = g.setdefault(agent, {"scopes": set(), "delegates_to": set()})
                if op == "grant_scope":
                    node["scopes"].add(value)
                elif op == "revoke_scope":
                    node["scopes"].discard(value)
                elif op == "add_delegation":
                    node["delegates_to"].add(value)
                elif op == "remove_delegation":
                    node["delegates_to"].discard(value)
                elif op:
                    fact.problems.append(f"unknown change {op!r}")
            for a in self.quarantined().value:
                g.pop(a, None)
            for node in g.values():
                node["delegates_to"] &= set(g)
        fact.value = g
        return fact

    def effective(self, g: dict, agent: str) -> set[str]:
        seen, stack, scopes = {agent}, [agent], set()
        while stack:
            a = stack.pop()
            scopes |= g.get(a, {}).get("scopes", set())
            for b in g.get(a, {}).get("delegates_to", set()):
                if b not in seen:
                    seen.add(b)
                    stack.append(b)
        return scopes

    def trifecta(self, current: bool = True) -> Fact:
        """Agents whose effective scopes cover private data + untrusted input + outbound communication."""
        perm = self.permissions(current)
        fact = Fact([], list(perm.evidence), perm.ok, list(perm.problems))
        reg = self.registry().value or {}
        classes = reg.get("scope_classes") or {}
        names = set(classes.values())
        need = [next((n for n in names if k in n.lower()), None) for k in ("private", "untrusted", "outbound")]
        if None in need:
            return fact.fail(f"scope classes {sorted(names)} lack private/untrusted/outbound")
        fact.value = sorted(a for a in perm.value
                            if set(need) <= {classes.get(s) for s in self.effective(perm.value, a)})
        return fact


_CACHE: dict[tuple, Facts] = {}
_CACHE_LOCK = threading.Lock()
KEY_PREFIXES = ("/logs/audit/", "/logs/archive/", "/fleet/", "/artifacts/", "/security/", "/plans/", "/contacts",
                "/runbook", "/AGENTS")


def facts_for(view: TaskView, judge: Judge, transcripts: dict[str, str] | None = None) -> Facts:
    """Facts for a task: the memoised facts are shared with every task whose fact-relevant files have the same blob
    hashes; direct file reads (f.v, f.text, f.csv_rows, ...) go through this task's own view, since files outside
    KEY_PREFIXES (/logs/access, /intel, /board, ...) differ between such tasks."""
    key = tuple(sorted((p, h) for p, h in view.files.items() if p.startswith(KEY_PREFIXES)))
    key += (id(judge), tuple(sorted((transcripts or {}).items())))
    with _CACHE_LOCK:
        f = _CACHE.get(key)
        if f is None:
            f = _CACHE[key] = Facts(view, judge, transcripts)
    if f.v is view:
        return f
    g = copy.copy(f)          # shares _memo and lock with the cached instance
    g.v = view
    return g
