"""Text extraction for download-only workspace formats (pdf, docx, xlsx, odt, ods), shared by the kit baseline and the
new harness. Locators follow the Task API citation rules: PDF `page`, docx/odt `paragraph` (1-based non-empty body
paragraphs), xlsx/ods `sheet` + `row` (1-based, as shown in the sheet)."""
from __future__ import annotations

MAX_SHEET_ROWS = 300  # per sheet; bigger registers need code, not context


def extract_text(path: str, data: bytes) -> str | None:
    """Text rendering of a pdf/docx/xlsx/odt/ods file, or None when there is no parser for its type.
    Parse errors propagate: the caller decides how to report a broken file."""
    low = path.lower()
    if low.endswith(".pdf"):
        return pdf_text(path, data)
    if low.endswith(".docx"):
        return docx_text(path, data)
    if low.endswith(".xlsx"):
        return xlsx_text(path, data)
    if low.endswith((".odt", ".ods")):
        return odf_text(path, data)
    return None


def pdf_text(path: str, data: bytes) -> str:
    import io
    from pypdf import PdfReader

    out = [f"[pdf extraction of {path}; cite by `page`]"]
    for i, page in enumerate(PdfReader(io.BytesIO(data)).pages, 1):
        text = (page.extract_text() or "").strip()
        out.append(f"--- page {i} ---\n{text or '(no text layer: scanned page, render it as an image to read it)'}")
    return "\n".join(out)


def docx_text(path: str, data: bytes) -> str:
    import io
    import re
    import zipfile
    from xml.etree import ElementTree as ET

    from docx import Document

    doc = Document(io.BytesIO(data))
    paras = [p.text for p in doc.paragraphs if p.text.strip()]
    out = [f"[docx extraction of {path}; body paragraphs numbered for citing by `paragraph`]"]
    out += [f"{i}. {t}" for i, t in enumerate(paras, 1)]
    for s in doc.sections:
        for label, part in (("header", s.header), ("footer", s.footer)):
            t = "\n".join(p.text for p in part.paragraphs if p.text.strip())
            if t:
                out.append(f"[{label}] {t}")
    zf = zipfile.ZipFile(io.BytesIO(data))
    for n in zf.namelist():
        if re.fullmatch(r"word/(comments|footnotes)\.xml", n):
            t = " ".join(x.strip() for x in ET.fromstring(zf.read(n)).itertext() if x.strip())
            if t:
                out.append(f"[{n.split('/')[-1]}] {t}")
    return "\n".join(out)


def xlsx_text(path: str, data: bytes) -> str:
    import io

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), data_only=True)  # cached values for formulas
    out = [f"[xlsx extraction of {path}; cite by `sheet` + `row`; values as stored (dates may be mis-typed numbers)]"]
    for ws in wb.worksheets:
        out.append(f"=== sheet {ws.title!r}{' [hidden]' if ws.sheet_state != 'visible' else ''} ({ws.max_row} rows) ===")
        for r, row in enumerate(ws.iter_rows(values_only=True), 1):
            if r > MAX_SHEET_ROWS:
                out.append(f"... {ws.max_row - MAX_SHEET_ROWS} more rows not shown (process the file with code)")
                break
            if any(v is not None and str(v).strip() for v in row):
                hidden = " [hidden]" if ws.row_dimensions[r].hidden else ""
                out.append(f"r{r}{hidden}: " + " | ".join("" if v is None else str(v) for v in row))
    return "\n".join(out)


def odf_text(path: str, data: bytes) -> str:
    import io
    import zipfile
    from xml.etree import ElementTree as ET

    ns_text = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    ns_table = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
    ns_office = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    body = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("content.xml")).find(f"{{{ns_office}}}body")

    ns_dc = "http://purl.org/dc/elements/1.1/"

    def text_of(el: ET.Element) -> str:
        """Text of an element without its comments (office:annotation)."""
        parts = [el.text or ""]
        for child in el:
            if child.tag != f"{{{ns_office}}}annotation":
                parts.append(text_of(child))
            parts.append(child.tail or "")
        return "".join(parts).strip()

    if path.lower().endswith(".odt"):
        # Numbering = top-level text:p / text:h of the body (tables and comments are not paragraphs), as in TASK_API.md.
        out = [f"[odt extraction of {path}; body paragraphs numbered for citing by `paragraph`]"]
        comments = []
        n = 0
        for el in body.find(f"{{{ns_office}}}text"):
            if el.tag in (f"{{{ns_text}}}p", f"{{{ns_text}}}h"):
                t = text_of(el)
                if t:
                    n += 1
                    out.append(f"{n}. {t}")
                for a in el.iter(f"{{{ns_office}}}annotation"):
                    who = a.findtext(f"{{{ns_dc}}}creator", "")
                    comments.append(f"[comment by {who} on paragraph {n}] " + " ".join(text_of(p) for p in a.iter(f"{{{ns_text}}}p")))
            elif el.tag == f"{{{ns_table}}}table":
                for row in el.iter(f"{{{ns_table}}}table-row"):
                    out.append("[table] " + " | ".join(text_of(c) for c in row))
        return "\n".join(out + comments)

    out = [f"[ods extraction of {path}; cite by `sheet` + `row`]"]
    for table in body.iter(f"{{{ns_table}}}table"):
        out.append(f"=== sheet {table.get(f'{{{ns_table}}}name')!r} ===")
        r = not_shown = 0
        for row in table.iter(f"{{{ns_table}}}table-row"):
            reps = int(row.get(f"{{{ns_table}}}number-rows-repeated", "1"))
            hidden = " [hidden]" if row.get(f"{{{ns_table}}}visibility", "visible") != "visible" else ""
            cells = []
            for c in row:
                cells += [text_of(c)] * min(int(c.get(f"{{{ns_table}}}number-columns-repeated", "1")), 50)
            while cells and not cells[-1]:
                cells.pop()
            if cells:
                for k in range(1, min(reps, 50) + 1):
                    if r + k <= MAX_SHEET_ROWS:
                        out.append(f"r{r + k}{hidden}: " + " | ".join(cells))
                    else:
                        not_shown += 1
            r += reps
        if not_shown:
            out.append(f"... {not_shown} more rows not shown (process the file with code)")
    return "\n".join(out)
