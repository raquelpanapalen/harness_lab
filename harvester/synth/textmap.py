"""One-pass vocabulary rewrite for a theme (see themes/*.json).

`Reskin.text(s)` swaps every hub-theme string for its theme counterpart in a single left-to-right pass, so a
replacement is never rewritten again (a map can swap A→B and B→A). Strings inside base64 runs and Unicode tag-character
runs (two encodings the attack texts use) are decoded, rewritten and re-encoded.

The literals are compiled into one trie-shaped regex: at each position only the branch for the next character is
tried, which keeps a ~400-entry map fast over a 100 MB run.
"""
from __future__ import annotations

import base64
import html
import binascii
import json
import re
from pathlib import Path

ALNUM_BEFORE = r"(?<![^\W_])"   # not preceded by a letter or digit (Unicode-aware)
ALNUM_AFTER = r"(?![^\W_])"     # not followed by a letter or digit


def _variants(old: str, new: str) -> list[tuple[str, str]]:
    out = [(old, new)]
    for f in (str.lower, str.upper, lambda s: s[:1].upper() + s[1:]):
        o, n = f(old), f(new)
        if o != old:
            out.append((o, n))
    return out


class _Trie:
    def __init__(self):
        self.kids: dict[str, _Trie] = {}
        self.ends: list[str] = []  # lookahead conditions that end a match here ("" = plain literal)

    def add(self, s: str, cond: str) -> None:
        node = self
        for ch in s:
            node = node.kids.setdefault(ch, _Trie())
        if cond not in node.ends:
            node.ends.append(cond)

    def regex(self) -> str:
        alts = [re.escape(ch) + kid.regex() for ch, kid in sorted(self.kids.items())]
        alts += self.ends  # after the longer branches: the longest match wins
        if not alts:
            return ""
        if len(alts) == 1:
            return alts[0] if not self.ends else f"(?:{alts[0]})"
        return "(?:" + "|".join(alts) + ")"


class Reskin:
    def __init__(self, theme: dict, extra: dict[str, str] | None = None):
        self.map: dict[str, str] = {}
        conds: dict[str, set[str]] = {}

        def put(old: str, new: str, cond: str) -> None:
            prev = self.map.get(old)
            if prev is not None and prev != new:
                return  # first definition wins (explicit entries come before generated variants)
            self.map[old] = new
            conds.setdefault(old, set()).add(cond)

        literals = dict(theme.get("literals", {}))
        literals.update(extra or {})
        for old, new in literals.items():
            for o, n in _variants(old, new):
                put(o, n, ALNUM_AFTER if o[-1:].isalnum() else "")
        for old, new in theme.get("agent_prefixes", {}).items():
            put(old, new, r"(?=-\d{2}(?![^\W_]))")
        for old, new in theme.get("org_roots", {}).items():
            put(old, new, r"(?= (?:[A-Z]|& ))")
            put(old.lower(), new.lower(), r"(?=[.-][a-z])")
        # a plain literal already matches everywhere a conditional one would
        trie = _Trie()
        for old, cs in conds.items():
            for c in ([ALNUM_AFTER] if ALNUM_AFTER in cs else [""] if "" in cs else sorted(cs)):
                trie.add(old, c)
        self.rx = re.compile(ALNUM_BEFORE + trie.regex())
        self.old_rx = self.rx  # the same pattern finds leftovers in rewritten text
        self.hits = 0

    # --- text --------------------------------------------------------------------------------------------
    def _plain(self, s: str) -> str:
        def sub(m: re.Match) -> str:
            self.hits += 1
            return self.map[m.group(0)]
        return self.rx.sub(sub, s)

    def text(self, s: str) -> str:
        if not s:
            return s
        s = _TAGS.sub(lambda m: _tag_encode(self.text(_tag_decode(m.group(0)))), s)
        s = _B64.sub(self._b64, s)
        return self._plain(s)

    def _b64(self, m: re.Match) -> str:
        raw = m.group(0)
        try:
            dec = base64.b64decode(raw + "=" * (-len(raw) % 4), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return raw
        if sum(c.isprintable() or c in "\n\t" for c in dec) < 0.97 * len(dec):
            return raw
        new = self.text(dec)
        if new == dec:
            return raw
        enc = base64.b64encode(new.encode()).decode()
        return enc if raw.endswith("=") else enc.rstrip("=")

    def leftovers(self, s: str) -> list[str]:
        """Hub-theme strings still present in rewritten text (should be none)."""
        return [m.group(0) for m in self.old_rx.finditer(s)]

    # --- structured text -----------------------------------------------------------------------------------
    def json_text(self, s: str) -> str:
        """Rewrite the string literals of JSON / JSONL text in place, keeping the layout (and so line numbers)."""
        def sub(m: re.Match) -> str:
            lit = m.group(0)
            try:
                val = json.loads(lit)
            except ValueError:
                return self.text(lit)
            new = self.text(val)
            if new == val:
                return lit
            return json.dumps(new, ensure_ascii="\\u" in lit)
        return _JSON_STR.sub(sub, s)

    def xml_text(self, s: str) -> str:
        """Rewrite the text nodes of an XML document (docx / odt / xlsx / ods parts)."""
        def sub(m: re.Match) -> str:
            raw = m.group(1)
            val = _xml_unescape(raw)
            new = self.text(val)
            return ">" + (raw if new == val else _xml_escape(new)) + "<"
        return _XML_TEXT.sub(sub, s)


_TAGS = re.compile("[\U000e0000-\U000e007f]{4,}")
_B64 = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/=_-])")
_JSON_STR = re.compile(r'"(?:[^"\\\n]|\\.)*"')
_XML_TEXT = re.compile(r">([^<]+)<")


def _tag_decode(s: str) -> str:
    return "".join(chr(ord(c) - 0xE0000) for c in s)


def _tag_encode(s: str) -> str:
    return "".join(chr(ord(c) + 0xE0000) if ord(c) < 0x80 else c for c in s)


def _xml_unescape(s: str) -> str:
    return html.unescape(s)


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_theme(name_or_path: str) -> dict:
    p = Path(name_or_path)
    if not p.exists():
        p = Path(__file__).parent / "themes" / f"{name_or_path}.json"
    return json.loads(p.read_text())
