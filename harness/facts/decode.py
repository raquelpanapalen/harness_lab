"""Decoders run before any judgment (SPEC §5): hidden or encoded text is made visible, so the judge sees what an agent
reading the artifact would act on. Pure functions, no model.

    reveal(text) -> text with zero-width characters removed, Unicode tag characters decoded, homoglyphs folded, %-escapes and HTML entities decoded,
                    and every decodable base64 / hex run appended as "[decoded base64: ...]".
"""
from __future__ import annotations

import base64
import binascii
import html
import re
import unicodedata
from urllib.parse import unquote

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad\u180e\u2061\u2062\u2063\u2064"), None)

# Cyrillic / Greek letters that render like Latin ones (the common confusables; NFKC does not fold these).
_HOMO = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d",
    "ɡ": "g", "һ": "h", "ӏ": "l", "ԛ": "q", "ԝ": "w", "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X", "У": "Y", "І": "I", "Ј": "J", "Ѕ": "S",
    "α": "a", "ο": "o", "ν": "v", "ρ": "p", "τ": "t", "υ": "u", "ι": "i", "κ": "k", "Α": "A", "Β": "B", "Ε": "E",
    "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
})

B64_RX = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/_-]{16,}={0,2}(?![A-Za-z0-9+/=_-])")
HEX_RX = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[\s:]?){12,}(?![0-9A-Fa-f])")
HEX_PLAIN_RX = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}){12,}(?![0-9A-Fa-f])")   # "ref:7265…" (no separators)
TAG_RX = re.compile("[\U000E0020-\U000E007E]+")   # Unicode tag characters: invisible copies of ASCII


def fold(text: str) -> str:
    """Zero-width characters out, homoglyphs and compatibility forms folded to plain Latin."""
    return unicodedata.normalize("NFKC", text.translate(ZERO_WIDTH)).translate(_HOMO)


def _printable(b: bytes) -> str | None:
    try:
        s = b.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if len(s) < 6:
        return None
    ok = sum(ch.isprintable() or ch in "\n\t" for ch in s)
    letters = sum(ch.isalpha() for ch in s)
    return s if ok / len(s) > 0.95 and letters / len(s) > 0.5 and " " in s else None


def decode_runs(text: str) -> list[tuple[str, str]]:
    """(kind, decoded text) for every base64 / hex run that decodes to readable text (nested once)."""
    out: list[tuple[str, str]] = []
    for m in B64_RX.finditer(text):
        s = m.group(0)
        for alt in (s, s.replace("-", "+").replace("_", "/")):
            try:
                d = _printable(base64.b64decode(alt + "=" * (-len(alt) % 4), validate=True))
            except (binascii.Error, ValueError):
                d = None
            if d:
                out.append(("base64", d))
                out += [(f"base64/{k}", x) for k, x in decode_runs(d)][:2]
                break
    seen = set()
    for m in list(HEX_RX.finditer(text)) + list(HEX_PLAIN_RX.finditer(text)):
        try:
            d = _printable(bytes.fromhex(re.sub(r"[\s:]", "", m.group(0))))
        except ValueError:
            d = None
        if d and d not in seen:
            seen.add(d)
            out.append(("hex", d))
    return out


def tag_text(text: str) -> str:
    """Text hidden in Unicode tag characters (U+E0020..E007E map to ASCII)."""
    return " ".join("".join(chr(ord(c) - 0xE0000) for c in m.group(0)) for m in TAG_RX.finditer(text))


def reveal(text: str) -> str:
    hidden = tag_text(text)
    t = fold(TAG_RX.sub("", text).replace("\U000E0001", "").replace("\U000E007F", ""))
    if hidden:
        t += f"\n[decoded unicode tags: {hidden}]"
    if "%" in t:
        t2 = unquote(t)
        if t2 != t:
            t = t + "\n[url-decoded: " + t2 + "]"
    if "&" in t:
        t = html.unescape(t)
    for kind, d in decode_runs(t):
        t += f"\n[decoded {kind}: {fold(d)}]"
    return t


def pdf_page_image(data: bytes, page: int) -> tuple[bytes, str] | None:
    """The largest image drawn on a PDF page (1-based), as (bytes, mime), without Pillow: a DCT (JPEG) stream is
    returned as is; an 8-bit DeviceRGB / DeviceGray Flate stream is re-wrapped as a PNG. Else None."""
    import io
    import struct
    import zlib

    from pypdf import PdfReader

    try:
        pg = PdfReader(io.BytesIO(data)).pages[page - 1]
        xo = pg["/Resources"]["/XObject"].get_object()
    except Exception:  # noqa: BLE001
        return None
    best = None
    for name in xo:
        o = xo[name].get_object()
        if o.get("/Subtype") != "/Image":
            continue
        f = o.get("/Filter")
        f = f[0] if isinstance(f, list) and len(f) == 1 else f
        w, h = int(o.get("/Width", 0)), int(o.get("/Height", 0))
        out = None
        if f == "/DCTDecode":
            out = (o._data, "image/jpeg")
        elif f == "/FlateDecode" and int(o.get("/BitsPerComponent", 8)) == 8:
            cs = o.get("/ColorSpace")
            ch = {"/DeviceRGB": 3, "/DeviceGray": 1}.get(cs if isinstance(cs, str) else None)
            if ch:
                raw = o.get_data()
                if len(raw) == w * h * ch:
                    rows = b"".join(b"\x00" + raw[y * w * ch:(y + 1) * w * ch] for y in range(h))

                    def chunk(t, d):
                        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
                    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2 if ch == 3 else 0,
                                                                            0, 0, 0))
                    png += chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
                    out = (png, "image/png")
        if out and (best is None or w * h > best[0]):
            best = (w * h, out)
    return best[1] if best else None
