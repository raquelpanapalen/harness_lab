"""Rewrite the text of a PDF in place: every string shown by Tj / TJ / ' / " is decoded with its font's mapping,
passed through the reskin, and encoded back.

- Type0 fonts (Identity-H) with a ToUnicode CMap: characters that the CMap lacks get a new code and a new CMap entry.
  The embedded subset has no glyph for them, so they render blank, but text extraction (all the harness does with a
  PDF) reads them correctly.
- Simple fonts (standard 14 / WinAnsi): cp1252.
- A TJ array whose text changes becomes one plain string (kerning dropped).
Fonts with other encodings are left alone; the caller checks the extracted text for leftovers.
"""
from __future__ import annotations

import io
import re
from typing import Callable

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, ByteStringObject, ContentStream, DecodedStreamObject, NameObject,
                           TextStringObject)


def _raw(obj) -> bytes:
    if isinstance(obj, TextStringObject):
        ob = getattr(obj, "_original_bytes", None)  # get_original_bytes() re-adds a UTF-16 BOM pypdf guessed
        return ob if ob is not None else obj.get_original_bytes()
    return bytes(obj)


class _CidCodec:
    def __init__(self, font):
        self.font = font
        self.to_uni: dict[int, str] = {}
        tu = font.get("/ToUnicode")
        if tu is not None:
            self._parse(tu.get_object().get_data().decode("latin-1"))
        self.from_uni = {v: k for k, v in self.to_uni.items() if len(v) == 1}
        self.dirty = False
        # the generator's codes follow the font's glyph order: ASCII code = ord - offset for one fixed offset
        offs = {k - ord(v) for k, v in self.to_uni.items() if len(v) == 1 and 0x20 < ord(v) < 0x7F}
        self.ascii_off = offs.pop() if len(offs) == 1 else None
        self.next_free = max(self.to_uni, default=0) + 0x200

    def _parse(self, cmap: str) -> None:
        for block in re.findall(r"beginbfchar(.*?)endbfchar", cmap, re.S):
            for src, dst in re.findall(r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", block):
                self.to_uni[int(src, 16)] = bytes.fromhex(dst).decode("utf-16-be", "replace")
        for block in re.findall(r"beginbfrange(.*?)endbfrange", cmap, re.S):
            for a, b, dst in re.findall(r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", block):
                base = int(dst, 16)
                for i, code in enumerate(range(int(a, 16), int(b, 16) + 1)):
                    self.to_uni[code] = chr(base + i)

    def decode(self, b: bytes) -> str | None:
        if len(b) % 2:
            return None
        out = []
        for i in range(0, len(b), 2):
            ch = self.to_uni.get(int.from_bytes(b[i:i + 2], "big"))
            if ch is None:
                return None
            out.append(ch)
        return "".join(out)

    def encode(self, s: str) -> bytes:
        out = bytearray()
        for ch in s:
            code = self.from_uni.get(ch)
            if code is None:
                if self.ascii_off is not None and 0x20 < ord(ch) < 0x7F and ord(ch) + self.ascii_off not in self.to_uni:
                    code = ord(ch) + self.ascii_off
                else:
                    while self.next_free in self.to_uni:
                        self.next_free += 1
                    code = self.next_free
                self.to_uni[code] = ch
                self.from_uni[ch] = code
                self.dirty = True
            out += code.to_bytes(2, "big")
        return bytes(out)

    def cmap_bytes(self) -> bytes:
        items = sorted(self.to_uni.items())
        lines = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
                 "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
                 "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
                 "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange"]
        for i in range(0, len(items), 100):
            chunk = items[i:i + 100]
            lines.append(f"{len(chunk)} beginbfchar")
            lines += [f"<{k:04x}> <{v.encode('utf-16-be').hex()}>" for k, v in chunk]
            lines.append("endbfchar")
        lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
        return "\n".join(lines).encode()


class _SimpleCodec:
    dirty = False

    def decode(self, b: bytes) -> str | None:
        return b.decode("cp1252", "replace")

    def encode(self, s: str) -> bytes:
        return s.encode("cp1252", "replace")


def _codec(font):
    font = font.get_object()
    sub = font.get("/Subtype")
    if sub == "/Type0" and font.get("/ToUnicode") is not None:
        return _CidCodec(font)
    if sub in ("/Type1", "/TrueType") and font.get("/ToUnicode") is None:
        enc = font.get("/Encoding")
        if enc is None or (isinstance(enc, str) and enc in ("/WinAnsiEncoding", "/StandardEncoding")):
            return _SimpleCodec()
    return None


def rewrite_pdf(data: bytes, tx: Callable[[str], str]) -> bytes | None:
    """The rewritten PDF, or None when nothing changed."""
    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter(clone_from=reader)
    changed = False
    codecs: dict[int, object] = {}
    for page in writer.pages:
        res = page.get("/Resources")
        fonts = (res.get_object().get("/Font") if res is not None else None) or {}
        page_codecs = {}
        for name, ref in fonts.items():
            key = id(ref.get_object())
            if key not in codecs:
                codecs[key] = _codec(ref)
            page_codecs[name] = codecs[key]
        contents = page.get_contents()
        if contents is None:
            continue
        cs = ContentStream(contents, writer)
        cur = None
        page_changed = False
        for i, (operands, op) in enumerate(cs.operations):
            if op == b"Tf" and operands:
                cur = page_codecs.get(operands[0])
                continue
            if cur is None or op not in (b"Tj", b"'", b'"', b"TJ") or not operands:
                continue
            if op == b"TJ":
                parts = [o for o in operands[0] if isinstance(o, (TextStringObject, ByteStringObject))]
                raw = b"".join(_raw(o) for o in parts)
            else:
                raw = _raw(operands[-1])
            text = cur.decode(raw)
            if text is None:
                continue
            new = tx(text)
            if new == text:
                continue
            enc = ByteStringObject(cur.encode(new))
            if op == b"TJ":
                operands[0] = ArrayObject([enc])
            else:
                operands[-1] = enc
            cs.operations[i] = (operands, op)
            page_changed = True
        if page_changed:
            page.replace_contents(cs)
            changed = True
    for c in codecs.values():
        if isinstance(c, _CidCodec) and c.dirty:
            st = DecodedStreamObject()
            st.set_data(c.cmap_bytes())
            c.font[NameObject("/ToUnicode")] = writer._add_object(st)
    meta = reader.metadata or {}
    new_meta = {k: tx(str(v)) for k, v in meta.items() if isinstance(v, str) and tx(str(v)) != str(v)}
    if new_meta:
        writer.add_metadata(new_meta)
        changed = True
    if not changed:
        return None
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def pdf_text(data: bytes) -> str:
    r = PdfReader(io.BytesIO(data))
    parts = [p.extract_text() or "" for p in r.pages]
    meta = r.metadata or {}
    parts += [str(v) for v in meta.values() if isinstance(v, str)]
    return "\n".join(parts)
