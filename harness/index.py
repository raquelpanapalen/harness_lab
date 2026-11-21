"""Workspace index (SPEC §3): every blob of the run parsed once into evidence units, SQLite tables and BM25 postings.

    idx = Index.build(client, run_id)          # SETUP: run manifest, download every distinct blob, parse eagerly
    view = idx.view(task_id)                   # one task's workspace: only the blobs in its own manifest
    view.search("devops-07 export", k=10)      # BM25 over evidence units -> [(score, Unit)]
    view.units("/fleet/agents.csv")            # evidence units of one file (PDFs parsed on first use)
    view.table("/fleet/agents.csv")            # SQLite table name; view.sql("SELECT ...") runs a query

An **evidence unit** is exactly what the grader cites (docs.md, "Submit"): a line (md/txt/html/jsonl), a record (csv:
first column, json: `id` field), sheet + row (xlsx/ods), paragraph (docx/odt), page (pdf), or a whole file (path only,
every file has one). `Unit.source()` is the citation; `Unit.alts` holds equivalent locators the grader also accepts
(a csv record is also its line). `untrusted` is set by structural signals only, never by marker phrases
(wayfinder/harness/03-injection-landscape.md): an audit `detail` of >= 15 words, a live URL with a path or query, an
Office comment, invisible PDF text (render mode 3) on a page that is not a scan.

Everything parsed is keyed by blob sha256, so per-task file variants never leak between tasks.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import re
import sqlite3
import threading
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

logging.getLogger("pypdf").setLevel(logging.ERROR)  # font warnings on real-world PDFs

TEXT_EXT = {"md", "txt", "csv", "json", "jsonl", "html"}
IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}

DETAIL_WORDS = 15
URL_RX = re.compile(r"https?://([A-Za-z0-9.-]+)(/[^\s)\]\"'<>]+|\?[^\s)\]\"'<>]+)")
TOKEN_RX = re.compile(r"[a-z0-9]+(?:[-_.@/:][a-z0-9]+)*")


def ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def tokens(text: str) -> list[str]:
    """Compound ids (`TOK-CIR-30`, `devops-07`, `a@b.example`) stay whole and are also split into their parts."""
    out = []
    for t in TOKEN_RX.findall(text.lower()):
        out.append(t)
        if not t.isalnum():
            out += [p for p in re.split(r"[-_.@/:]", t) if p]
    return out


# --- evidence units -------------------------------------------------------------------------------------------------
@dataclass(slots=True)
class Unit:
    path: str
    kind: str                   # file | line | record | row | paragraph | page | part
    loc: dict                   # the locator the grader wants: {"line": 3}, {"record": "TOK-1"}, {"sheet", "row"}, ...
    text: str
    untrusted: bool = False
    why: tuple[str, ...] = ()   # which structural signals set `untrusted`
    alts: tuple[dict, ...] = ()  # other locators the grader accepts for the same evidence
    uid: str = ""               # "<sha12>:<i>", stable within a run

    def source(self) -> dict:
        return {"path": self.path, **self.loc}

    def sources(self) -> list[dict]:
        return [self.source()] + [{"path": self.path, **a} for a in self.alts]


def _flags(text: str, detail: str | None = None) -> tuple[str, ...]:
    why = []
    if detail is not None and len(detail.split()) >= DETAIL_WORDS:
        why.append("long-detail")
    if URL_RX.search(text):
        why.append("live-url")
    return tuple(why)


def _unit(path: str, kind: str, loc: dict, text: str, why: tuple[str, ...] = (), alts: tuple = ()) -> Unit:
    return Unit(path, kind, loc, text, bool(why), why, alts)


def file_unit(path: str, text: str = "") -> Unit:
    return _unit(path, "file", {}, f"{path} {text}".strip())


@dataclass
class Table:
    name: str
    columns: list[str]
    rows: list[list]           # values; the last column is the locator (_line or _row)


@dataclass
class Doc:
    """One parsed blob (path-dependent only through Unit.path, set when a task view asks)."""
    sha: str
    ext: str
    size: int
    units: list[Unit] = field(default_factory=list)
    tables: dict[str, Table] = field(default_factory=dict)  # sheet name ("" for csv/jsonl) -> table
    lines: list[str] | None = None  # text files: the decoded lines (served by read/search)
    pages: int | None = None        # pdf page count, None when the platform would not show one
    parsed: bool = True             # False for a lazy blob not parsed yet
    error: str | None = None
    raw: bytes | None = None        # a lazy blob's bytes until it is parsed
    # BM25
    postings: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    lengths: list[int] = field(default_factory=list)

    def build_postings(self) -> None:
        post: dict[str, list[tuple[int, int]]] = {}
        self.lengths = []
        for i, u in enumerate(self.units):
            tf = Counter(tokens(u.text))
            self.lengths.append(sum(tf.values()))
            for t, n in tf.items():
                post.setdefault(t, []).append((i, n))
        self.postings = post


# --- parsers --------------------------------------------------------------------------------------------------------
def parse(path: str, sha: str, data: bytes, lazy: bool = True) -> Doc:
    e = ext(path)
    doc = Doc(sha=sha, ext=e, size=len(data))
    try:
        if e in TEXT_EXT:
            text = data.decode("utf-8", errors="replace")
            doc.lines = text.splitlines()
            {"csv": _csv, "json": _json, "jsonl": _jsonl}.get(e, _lines)(path, text, doc)
        elif e == "xlsx":
            _xlsx(path, data, doc)
        elif e == "ods":
            _ods(path, data, doc)
        elif e == "docx":
            _docx(path, data, doc)
        elif e == "odt":
            _odt(path, data, doc)
        elif e == "pdf":
            doc.pages = pdf_shown_pages(data)
            if lazy:
                doc.parsed = False
            else:
                _pdf(path, data, doc)
    except Exception as ex:  # noqa: BLE001 - a broken file still gets its file unit and stays citable by path
        doc.error = f"{type(ex).__name__}: {ex}"
    doc.units.insert(0, file_unit(path))
    for i, u in enumerate(doc.units):
        u.uid = f"{sha[:12]}:{i}"
    doc.build_postings()
    return doc


def _lines(path: str, text: str, doc: Doc) -> None:
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip():
            doc.units.append(_unit(path, "line", {"line": i}, line, _flags(line)))


def _jsonl(path: str, text: str, doc: Doc) -> None:
    rows, cols = [], {}
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            ev = None
        detail = ev.get("detail") if isinstance(ev, dict) and isinstance(ev.get("detail"), str) else None
        doc.units.append(_unit(path, "line", {"line": i}, line, _flags(line, detail)))
        if isinstance(ev, dict):
            for k in ev:
                cols.setdefault(k, None)
            rows.append((ev, i))
    if rows:
        names = list(cols)
        doc.tables[""] = Table("", names + ["_line"], [[_cell(ev.get(k)) for k in names] + [i] for ev, i in rows])


FREE_TEXT = ("detail", "text", "body", "message", "note", "summary")


def _event_key(u: Unit) -> tuple:
    """(structured fields, free-text fields) of a jsonl event unit, or (None, None)."""
    if u.kind != "line":
        return None, None
    try:
        ev = json.loads(u.text)
    except ValueError:
        return None, None
    if not isinstance(ev, dict):
        return None, None
    key = tuple(sorted((k, json.dumps(v, sort_keys=True)) for k, v in ev.items() if k not in FREE_TEXT))
    return key, tuple(str(ev.get(k)) for k in FREE_TEXT)


def _cell(v):
    if v is None or isinstance(v, (int, float, str)):
        return v
    return json.dumps(v, ensure_ascii=False)


_TIMELIKE = re.compile(r"^\d{1,4}[./-]\d{1,2}[./-]\d{1,4}")


def _csv(path: str, text: str, doc: Doc) -> None:
    """One unit per data row. Rows are cited by `record` (the first column, docs) when that column is an id: unique
    and not a date or time; otherwise by `line` (the CET archive log starts with its timestamp). The other locator is
    kept as the alternative. A quoted field may span lines, so lines are tracked by the reader."""
    reader = csv.reader(io.StringIO(text))
    try:
        head = next(reader)
    except StopIteration:
        return
    detail_cols = [i for i, h in enumerate(head) if h.strip().lower() == "detail"]
    width, rows, parsed, start = len(head), [], [], reader.line_num + 1
    for row in reader:
        line, start = start, reader.line_num + 1
        if any(c.strip() for c in row):
            parsed.append((line, row))
    firsts = [r[0].strip() for _, r in parsed if r and r[0].strip()]
    ids = len(set(firsts)) == len(firsts) and not any(_TIMELIKE.match(x) for x in firsts)
    for line, row in parsed:
        raw = ",".join(row)
        detail = " ".join(row[i] for i in detail_cols if i < len(row)) if detail_cols else None
        rec = row[0].strip() if row else ""
        if rec and ids:
            loc, alts = {"record": rec}, ({"line": line},)
        else:
            loc, alts = {"line": line}, (({"record": rec},) if rec else ())
        doc.units.append(_unit(path, "record", loc, raw, _flags(raw, detail), alts))
        rows.append((row + [""] * width)[:width] + [line])
    doc.tables[""] = Table("", _colnames(head) + ["_line"], rows)


def _colnames(head: list) -> list[str]:
    out, seen = [], set()
    for i, h in enumerate(head, 1):
        n = str(h).strip() if h is not None and str(h).strip() else f"col{i}"
        while n.lower() in seen or n.lower() in ("_line", "_row"):
            n += "_"
        seen.add(n.lower())
        out.append(n)
    return out


def _json(path: str, text: str, doc: Doc) -> None:
    """Records are objects carrying an `id` (registry agents, approvals); the whole file is cited by path."""
    try:
        data = json.loads(text)
    except ValueError:
        return _lines(path, text, doc)
    seen: set[str] = set()

    def walk(x, depth: int, key: str) -> None:
        if depth > 6:
            return
        if isinstance(x, dict):
            rid = x.get("id")
            if isinstance(rid, (str, int)) and str(rid) not in seen:
                seen.add(str(rid))
                body = json.dumps(x, ensure_ascii=False)
                doc.units.append(_unit(path, "record", {"record": str(rid)}, body, _flags(body)))
                return
            for k, v in x.items():
                walk(v, depth + 1, str(k))
        elif isinstance(x, list):
            for v in x:
                walk(v, depth + 1, key)
        elif isinstance(x, str) and len(x.split()) >= 4:
            # Prose outside an id'd record (an MCP tool description, a note): citable by path only.
            doc.units.append(_unit(path, "part", {}, f"{key}: {x}", _flags(x)))

    walk(data, 0, "")


def _sheet_units(path: str, doc: Doc, sheet: str, rows: list[tuple[int, list[str], bool, list[str]]]) -> None:
    """rows: (1-based row number, cell texts, hidden, comments on the row)."""
    tab_rows, head = [], None
    # The header is the first row as wide as the table (title and note rows above it are text, not columns).
    width = max((sum(1 for c in cells if c.strip()) for _, cells, _, _ in rows), default=0)
    head_at = next((r for r, cells, _, _ in rows if sum(1 for c in cells if c.strip()) >= max(2, width * 0.6)), None)
    for r, cells, hidden, comments in rows:
        if not any(c.strip() for c in cells) and not comments:
            continue
        text = " | ".join(cells) + (" [hidden row]" if hidden else "")
        why = _flags(text)
        if comments:
            text += " [cell comment: " + " / ".join(comments) + "]"
            why = tuple(dict.fromkeys(why + _flags(text) + ("office-comment",)))
        first = cells[0].strip() if cells else ""
        doc.units.append(_unit(path, "row", {"sheet": sheet, "row": r}, f"[{sheet}] {text}", why,
                               ({"record": first},) if first else ()))
        if head_at is None or r < head_at:
            continue
        if head is None:
            head = cells
        else:
            tab_rows.append(cells + [r])
    if head is not None:
        names = _colnames(head)
        doc.tables[sheet] = Table(sheet, names + ["_row"],
                                  [(c[:-1] + [""] * len(names))[:len(names)] + [c[-1]] for c in tab_rows])


def _xlsx(path: str, data: bytes, doc: Doc) -> None:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), data_only=True)
    for ws in wb.worksheets:
        rows = []
        hidden_sheet = ws.sheet_state != "visible"
        for r, row in enumerate(ws.iter_rows(min_row=1, max_row=ws.max_row), 1):
            cells = ["" if c.value is None else str(c.value) for c in row]
            comments = [c.comment.text.strip() for c in row if getattr(c, "comment", None) and c.comment.text]
            rows.append((r, cells, hidden_sheet or bool(ws.row_dimensions[r].hidden), comments))
        _sheet_units(path, doc, ws.title, rows)


NS = {"text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
      "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
      "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0"}


def _odf_text(el: ET.Element) -> tuple[str, list[str]]:
    """(text without annotations, annotation texts) of an ODF element."""
    notes: list[str] = []

    def rec(e: ET.Element) -> str:
        parts = [e.text or ""]
        for c in e:
            if c.tag == f"{{{NS['office']}}}annotation":
                notes.append(" ".join(" ".join(p.itertext()).strip() for p in c.iter(f"{{{NS['text']}}}p")).strip())
            else:
                parts.append(rec(c))
            parts.append(c.tail or "")
        return "".join(parts)

    return rec(el).strip(), [n for n in notes if n]


def _ods(path: str, data: bytes, doc: Doc) -> None:
    body = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("content.xml")).find(f"{{{NS['office']}}}body")
    t = NS["table"]
    for table in body.iter(f"{{{t}}}table"):
        rows, r = [], 0
        for row in table.iter(f"{{{t}}}table-row"):
            reps = int(row.get(f"{{{t}}}number-rows-repeated", "1"))
            hidden = row.get(f"{{{t}}}visibility", "visible") != "visible"
            cells, notes = [], []
            for c in row:
                txt, n = _odf_text(c)
                cells += [txt] * min(int(c.get(f"{{{t}}}number-columns-repeated", "1")), 50)
                notes += n
            while cells and not cells[-1]:
                cells.pop()
            if cells or notes:
                for k in range(1, min(reps, 50) + 1):
                    rows.append((r + k, cells, hidden, notes))
            r += reps
        _sheet_units(path, doc, table.get(f"{{{t}}}name") or "", rows)


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx(path: str, data: bytes, doc: Doc) -> None:
    from docx import Document

    zf = zipfile.ZipFile(io.BytesIO(data))
    comments: dict[str, str] = {}
    if "word/comments.xml" in zf.namelist():
        for c in ET.fromstring(zf.read("word/comments.xml")).iter(f"{W}comment"):
            comments[c.get(f"{W}id")] = " ".join(x.strip() for x in c.itertext() if x.strip())
    anchored: set[str] = set()
    d = Document(io.BytesIO(data))
    n = 0
    for p in d.paragraphs:
        if not p.text.strip():
            continue
        n += 1
        ids = [e.get(f"{W}id") for e in p._p.iter() if e.tag in (f"{W}commentRangeStart", f"{W}commentReference")]
        notes = [comments[i] for i in dict.fromkeys(ids) if i in comments]
        anchored |= set(ids)
        text, why = p.text, _flags(p.text)
        if notes:
            text += " [comment: " + " / ".join(notes) + "]"
            why = tuple(dict.fromkeys(why + _flags(text) + ("office-comment",)))
        doc.units.append(_unit(path, "paragraph", {"paragraph": n}, text, why))
    for cid, txt in comments.items():
        if cid not in anchored and txt:
            doc.units.append(_unit(path, "part", {}, f"[comment] {txt}", ("office-comment",) + _flags(txt)))
    for s in d.sections:
        for label, part in (("header", s.header), ("footer", s.footer)):
            t = "\n".join(p.text for p in part.paragraphs if p.text.strip())
            if t:
                doc.units.append(_unit(path, "part", {}, f"[{label}] {t}", _flags(t)))
    if "word/footnotes.xml" in zf.namelist():
        t = " ".join(x.strip() for x in ET.fromstring(zf.read("word/footnotes.xml")).itertext() if x.strip())
        if t:
            doc.units.append(_unit(path, "part", {}, f"[footnotes] {t}", _flags(t)))
    for tbl in d.tables:
        for row in tbl.rows:
            t = " | ".join(c.text.strip() for c in row.cells)
            if t.strip(" |"):
                doc.units.append(_unit(path, "part", {}, f"[table] {t}", _flags(t)))


def _odt(path: str, data: bytes, doc: Doc) -> None:
    body = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("content.xml")).find(f"{{{NS['office']}}}body")
    n = 0
    for el in body.find(f"{{{NS['office']}}}text"):
        if el.tag in (f"{{{NS['text']}}}p", f"{{{NS['text']}}}h"):
            text, notes = _odf_text(el)
            if text:
                n += 1
            why = _flags(text)
            if notes:
                text += " [comment: " + " / ".join(notes) + "]"
                why = tuple(dict.fromkeys(why + _flags(text) + ("office-comment",)))
            if text:
                doc.units.append(_unit(path, "paragraph" if n else "part", {"paragraph": n} if n else {}, text, why))
        elif el.tag == f"{{{NS['table']}}}table":
            for row in el.iter(f"{{{NS['table']}}}table-row"):
                cells = [_odf_text(c)[0] for c in row]
                t = " | ".join(cells)
                if t.strip(" |"):
                    doc.units.append(_unit(path, "part", {}, f"[table] {t}", _flags(t)))


def pdf_shown_pages(data: bytes) -> int | None:
    """The page count the platform shows in `tree`/`list`. It shows none for PDFs with incremental updates (`/Prev`)
    or object streams (measured on the recorded trees: 285 shown, 82 omitted, no exceptions)."""
    if b"/Prev" in data or b"/ObjStm" in data:
        return None
    try:
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:  # noqa: BLE001
        return None


_TR3 = re.compile(rb"(?<![\d.])3\s+Tr\b")


def _pdf(path: str, data: bytes, doc: Doc) -> None:
    """One unit per page. Invisible text (render mode 3) flags the page unless the page is a scan (an image with an
    OCR layer: 18 real government PDFs in the dev runs)."""
    from pypdf import PdfReader

    for i, page in enumerate(PdfReader(io.BytesIO(data)).pages, 1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001
            text = ""
        why: tuple[str, ...] = ()  # no live-url flag: real-world reports cite URLs (95 % of its false positives)
        try:
            content = page.get_contents()
            raw = content.get_data() if content is not None else b""
        except Exception:  # noqa: BLE001
            raw = b""
        if _TR3.search(raw) and not _has_image(page):
            why = why + ("invisible-pdf-text",)
        doc.units.append(_unit(path, "page", {"page": i}, text or "(no text layer: scanned page)", why))


def _has_image(page) -> bool:
    try:
        xo = page.get("/Resources", {}).get("/XObject", {})
        return any(xo[k].get_object().get("/Subtype") == "/Image" for k in xo)
    except Exception:  # noqa: BLE001
        return False


# --- the index ------------------------------------------------------------------------------------------------------
class Index:
    """Every blob of one run, parsed. Thread-safe; tasks read it through `view(task_id)`."""

    def __init__(self, client=None, run_id: str = ""):
        self.client, self.run_id = client, run_id
        self.docs: dict[str, Doc] = {}
        self.files: dict[str, dict[str, str]] = {}  # task -> path -> sha
        self.lock = threading.RLock()
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db_lock = threading.Lock()
        self.stats: dict = {}
        self._views: dict[str, TaskView] = {}

    # -- setup --
    @classmethod
    def build(cls, client, run_id: str, workers: int = 16) -> Index:
        """SETUP (SPEC §3): the run manifest, every distinct blob downloaded once (blob cache), parsed eagerly except
        PDFs and images."""
        t0 = time.time()
        idx = cls(client, run_id)
        m = client.run_manifest(run_id)
        owner: dict[str, tuple[str, str]] = {}
        for t in m.get("tasks", []):
            idx.files[t["task_id"]] = dict(t["files"])
            for p, h in t["files"].items():
                owner.setdefault(h, (t["task_id"], p))
        t1 = time.time()

        def load(h: str) -> None:
            tid, p = owner[h]
            idx.add(p, h, client.blob(run_id, h, tid))

        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(load, owner))
        tampered = idx.flag_variants()
        idx.stats = {"blobs": len(owner), "tasks": len(idx.files), "units": sum(len(d.units) for d in idx.docs.values()),
                     "tampered_units": tampered, "download_s": round(t1 - t0, 1), "setup_s": round(time.time() - t0, 1),
                     "errors": {owner[h][1]: d.error for h, d in idx.docs.items() if d.error}}
        return idx

    def flag_variants(self) -> int:
        """Cross-task signal: within a run every task gets the same base files plus its own variants. A jsonl event
        whose structured fields equal an event of the run's base version (the version most tasks have) but whose
        free text (`detail`, `text`, ...) differs was rewritten for this task: it is flagged `variant-text`."""
        by_path: dict[str, Counter] = {}
        for files in self.files.values():
            for p, h in files.items():
                if ext(p) == "jsonl":
                    by_path.setdefault(p, Counter())[h] += 1
        n = 0
        for p, shas in by_path.items():
            (base, nb), = shas.most_common(1)
            if len(shas) < 2 or nb < 2 or base not in self.docs:
                continue
            ref = {k: t for k, t in map(_event_key, self.docs[base].units) if k is not None}
            for h in shas:
                if h == base or h not in self.docs:
                    continue
                for u in self.docs[h].units:
                    k, t = _event_key(u)
                    if k is not None and k in ref and ref[k] != t and "variant-text" not in u.why:
                        u.why += ("variant-text",)
                        u.untrusted = True
                        n += 1
        return n

    def add(self, path: str, sha: str, data: bytes) -> Doc:
        with self.lock:
            if sha in self.docs:
                return self.docs[sha]
        doc = parse(path, sha, data)
        doc.raw = data if not doc.parsed else None
        with self.lock:
            if sha in self.docs:
                return self.docs[sha]
            self.docs[sha] = doc
        self._load_tables(doc)
        return doc

    def _load_tables(self, doc: Doc) -> None:
        with self.db_lock:
            for i, (sheet, tab) in enumerate(doc.tables.items()):
                tab.name = f"b_{doc.sha[:16]}" + (f"_s{i}" if doc.ext in ("xlsx", "ods") else "")
                cols = ", ".join(f'"{c.replace(chr(34), "")}"' for c in tab.columns)
                self.db.execute(f'DROP TABLE IF EXISTS "{tab.name}"')
                self.db.execute(f'CREATE TABLE "{tab.name}" ({cols})')
                width = len(tab.columns)
                self.db.executemany(f'INSERT INTO "{tab.name}" VALUES ({", ".join("?" * width)})',
                                    [(r + [None] * width)[:width] for r in tab.rows])
                tab.rows = []  # the data lives in SQLite now
            self.db.commit()

    def ensure_parsed(self, path: str, sha: str) -> Doc:
        """Parse a lazy blob (PDF) on first use."""
        doc = self.docs[sha]
        if doc.parsed:
            return doc
        with self.lock:
            if doc.parsed:
                return doc
            full = parse(path, sha, doc.raw, lazy=False)
            full.parsed, full.raw = True, None
            self.docs[sha] = full
            for v in self._views.values():
                v._stats = None
            return full

    def attach_text(self, sha: str, path: str, text: str) -> int:
        """Attach a transcript (image read by the vision model, build ticket 08) to the blob's Doc: one unit per
        non-empty line, cited by path only (loc {}), always untrusted. Replaces an earlier transcript of the same blob.
        Returns the number of units added."""
        with self.lock:
            doc = self.docs.get(sha)
            if doc is None:
                return 0
            base = [u for u in doc.units if "image-transcript" not in u.why]
            extra = [_unit(path, "part", {}, line.strip(), ("image-transcript",)) for line in text.splitlines()
                     if line.strip()]
            doc.units = base + extra
            for i, u in enumerate(doc.units):
                u.uid = f"{sha[:12]}:{i}"
            doc.lines = None
            doc.build_postings()
            for v in self._views.values():
                v._stats = None
            return len(extra)

    # -- per task --
    def view(self, task_id: str) -> TaskView:
        with self.lock:
            if task_id not in self._views:
                self._views[task_id] = TaskView(self, task_id)
            return self._views[task_id]

    def refresh(self, task_id: str) -> None:
        """After a state-changing tool call: re-read the task's manifest and index files that changed."""
        if self.client is None:
            return
        files = {p: e["sha256"] for p, e in self.client.task_manifest(self.run_id, task_id, refresh=True).items()
                 if e.get("sha256")}
        for p, h in files.items():
            if h not in self.docs:
                self.add(p, h, self.client.blob(self.run_id, h, task_id))
        with self.lock:
            self.files[task_id] = files
            if task_id in self._views:
                self._views[task_id]._stats = None


class TaskView:
    """One task's workspace inside the index: only the blobs of its own manifest."""

    K1, B = 1.2, 0.75

    def __init__(self, index: Index, task_id: str):
        self.index, self.task_id = index, task_id
        self._stats: tuple | None = None
        self._handles: dict[str, Unit] = {}
        self._by_uid: dict[str, str] = {}

    @property
    def files(self) -> dict[str, str]:
        return self.index.files.get(self.task_id, {})

    def paths(self) -> list[str]:
        return sorted(self.files)

    def doc(self, path: str, parse_lazy: bool = True) -> Doc | None:
        sha = self.files.get(path)
        if sha is None or sha not in self.index.docs:
            return None
        return self.index.ensure_parsed(path, sha) if parse_lazy else self.index.docs[sha]

    def units(self, path: str) -> list[Unit]:
        """Evidence units of one file, carrying this task's path (a blob's units are shared by every task)."""
        d = self.doc(path)
        if d is None:
            return []
        if d.units and d.units[0].path == path:
            return d.units
        return [Unit(path, u.kind, u.loc, u.text, u.untrusted, u.why, u.alts, u.uid) for u in d.units]

    def all_units(self, parse_lazy: bool = False):
        for p in self.paths():
            d = self.doc(p, parse_lazy)
            if d is not None:
                yield from (self.units(p) if parse_lazy or d.parsed else self.units_unparsed(p))

    def units_of_sha(self, path: str, sha: str) -> list[Unit]:
        """Units of another version of a file (e.g. the run's base version), for comparisons."""
        if sha not in self.index.docs:
            return []
        return self.index.ensure_parsed(path, sha).units

    def units_unparsed(self, path: str) -> list[Unit]:
        d = self.doc(path, parse_lazy=False)
        return [] if d is None else [Unit(path, u.kind, u.loc, u.text, u.untrusted, u.why, u.alts, u.uid)
                                      for u in d.units]

    # -- handles: the short labels the model sees and cites (E1, E2, ...) --
    def handle(self, u: Unit) -> str:
        with self.index.lock:
            h = self._by_uid.get(u.uid)
            if h is None:
                h = f"E{len(self._handles) + 1}"
                self._handles[h], self._by_uid[u.uid] = u, h
            return h

    def resolve(self, handle: str) -> Unit | None:
        return self._handles.get(handle.strip())

    # -- tables --
    def table(self, path: str, sheet: str | None = None) -> str | None:
        """SQLite table name of a csv / jsonl file or of one sheet (first sheet when none is named)."""
        d = self.doc(path, parse_lazy=False)
        if d is None or not d.tables:
            return None
        if sheet is None:
            return next(iter(d.tables.values())).name
        t = next((t for s, t in d.tables.items() if s.casefold() == sheet.casefold()), None)
        return t.name if t else None

    def sheets(self, path: str) -> list[str]:
        d = self.doc(path, parse_lazy=False)
        return list(d.tables) if d else []

    def sql(self, query: str, params: tuple = ()) -> list[tuple]:
        with self.index.db_lock:
            return self.index.db.execute(query, params).fetchall()

    # -- BM25 --
    def _corpus(self) -> tuple[Counter, int, float]:
        if self._stats is None:
            df: Counter = Counter()
            n = total = 0
            for p in self.paths():
                d = self.doc(p, parse_lazy=False)
                if d is None:
                    continue
                for t, post in d.postings.items():
                    df[t] += len(post)
                n += len(d.lengths)
                total += sum(d.lengths)
            self._stats = (df, n, total / n if n else 1.0)
        return self._stats

    def search(self, query: str, path: str | None = None, k: int = 10) -> list[tuple[float, Unit]]:
        """BM25 over this task's evidence units; `path` limits it to a file or a directory prefix."""
        q = list(dict.fromkeys(tokens(query)))
        if not q:
            return []
        df, n, avg = self._corpus()
        scope = path.rstrip("/") if path and path != "/" else None
        hits: list[tuple[float, str, int]] = []
        for p in self.paths():
            if scope and not (p == scope or p.startswith(scope + "/")):
                continue
            d = self.doc(p, parse_lazy=bool(scope))  # PDFs are parsed when a search targets them
            if d is None:
                continue
            scores: dict[int, float] = {}
            for t in q:
                post = d.postings.get(t)
                if not post:
                    continue
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                for i, tf in post:
                    dl = d.lengths[i]
                    scores[i] = scores.get(i, 0.0) + idf * tf * (self.K1 + 1) / (
                        tf + self.K1 * (1 - self.B + self.B * dl / avg))
            hits += [(s, p, i) for i, s in scores.items()]
        hits.sort(key=lambda x: -x[0])
        out = []
        for s, p, i in hits[:k]:
            u = self.doc(p, parse_lazy=False).units[i]
            out.append((round(s, 3), Unit(p, u.kind, u.loc, u.text, u.untrusted, u.why, u.alts, u.uid)))
        return out

    # -- citations --
    def find(self, source: dict) -> Unit | None:
        """The evidence unit a citation points at, by the grader's matching rules (line ±2, exact otherwise)."""
        p = "/" + str(source.get("path", "")).lstrip("/")
        units = self.units(p)
        keys = [k for k in ("line", "page", "record", "sheet", "row", "paragraph") if k in source]
        if not keys:
            return units[0] if units else None
        best = None
        for u in units:
            for s in u.sources():
                if _match(s, source):
                    if "line" not in source:
                        return u
                    d = abs(int(s["line"]) - int(source["line"]))
                    if best is None or d < best[0]:
                        best = (d, u)
        return best[1] if best else None


def _match(s: dict, e: dict) -> bool:
    """The grader's locator match (harvester/replay/grade.py `_src_match`, docs.md: line ±2)."""
    if "line" in e:
        try:
            return "line" in s and abs(int(s["line"]) - int(e["line"])) <= 2
        except (TypeError, ValueError):
            return False
    if "page" in e:
        return str(s.get("page")) == str(e["page"])
    if "record" in e:
        return "record" in s and str(s["record"]).strip().casefold() == str(e["record"]).strip().casefold()
    if "sheet" in e or "row" in e:
        return str(s.get("sheet", "")).casefold() == str(e.get("sheet", "")).casefold() and \
            str(s.get("row")) == str(e.get("row"))
    if "paragraph" in e:
        return str(s.get("paragraph")) == str(e["paragraph"])
    return not any(k in s for k in ("line", "page", "record", "sheet", "row", "paragraph"))
