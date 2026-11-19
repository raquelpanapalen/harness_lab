"""Images for a reskinned run: transcribe the original once (fixed model, cached by sha256), rewrite the transcript,
draw it again as a photographed sheet.

The redraw keeps what the tasks read from an image (every line of text, the stamps such as OPEN / CLOSED / VOID,
signatures, handwritten notes, struck-through lines) but not the original layout. A transcription error becomes part
of the new image, so the rebuilt run reports how closely each open ticket's transcript matched its known request.
"""
from __future__ import annotations

import hashlib
import io
import json
import random
import re
import sys
import threading
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

FONT_DIR = Path("/usr/share/fonts")
FONTS = {
    "title": "truetype/noto/NotoSans-Bold.ttf",
    "mono": "truetype/noto/NotoSansMono-Regular.ttf",
    "hand": "opentype/malayalam/Chilanka-Regular.otf",
    "stamp": "truetype/noto/NotoSansMono-Bold.ttf",
}
STAMP_WORDS = ("OPEN", "CLOSED", "APPROVED", "VOID", "REVOKED", "SUPERSEDED", "DRAFT", "COPY", "CANCELLED", "DENIED",
               "REJECTED", "VERIFIED", "RECEIVED", "PAID", "FILED")


# --- transcription ------------------------------------------------------------------------------------------------
class Transcripts:
    """sha256 -> transcript, persisted as JSON. Seeded from the harness's vision eval cache (same prompt)."""

    def __init__(self, path: Path, seed_files: list[Path] = ()):
        self.path = path
        self.data: dict[str, str] = {}
        for f in seed_files:
            if f.exists():
                for sha, v in json.loads(f.read_text()).items():
                    self.data.setdefault(sha, v["text"] if isinstance(v, dict) else v)
        if path.exists():
            self.data.update(json.loads(path.read_text()))
        self.lock = threading.Lock()
        self.cost = 0.0
        self.calls = 0

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=0))
        tmp.replace(self.path)

    def fill(self, items: dict[str, tuple[str, bytes]], workers: int = 8, log=print) -> None:
        """Transcribe every (sha -> (path, bytes)) not cached yet."""
        from arena import llm
        from harness.vision import MAX_TOKENS, PROMPT, data_url

        todo = [(sha, p, b) for sha, (p, b) in items.items() if sha not in self.data]
        if not todo:
            return
        log(f"[images] transcribing {len(todo)} images with {llm.MAIN_MODEL}")
        tracker = llm.CostTracker() if hasattr(llm, "CostTracker") else None

        done = [0]

        def one(job):
            sha, path, data = job
            msg = llm.chat([{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": data_url(path, data)}}]}],
                max_tokens=MAX_TOKENS, temperature=0.0, tracker=tracker, timeout=90)
            text = (msg.get("content") or "").strip()
            with self.lock:
                self.data[sha] = text
                self.calls += 1
                done[0] += 1
                if done[0] % 20 == 0:
                    self.save()
                    log(f"[images] {done[0]}/{len(todo)}")

        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(one, todo))
        self.save()
        if tracker is not None:
            self.cost = tracker.total
        log(f"[images] done: {done[0]} transcribed, cost ${self.cost:.4f}")


# --- transcript structure -------------------------------------------------------------------------------------------
def split_marks(transcript: str) -> tuple[list[str], str]:
    lines = transcript.strip().splitlines()
    marks = ""
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip().upper().startswith("MARKS:"):
            marks = lines[i].split(":", 1)[1].strip()
            lines = lines[:i]
            break
    while lines and not lines[-1].strip():
        lines.pop()
    return lines, marks


def with_request(transcript: str, request: str) -> str:
    """Put the known request text of an open ticket in place of its transcribed one."""
    lines, marks = split_marks(transcript)
    for i, l in enumerate(lines):
        if re.match(r"\s*Request\s*:", l, re.I):
            rest = l.split(":", 1)[1].strip()
            body = [request] if not rest else [f"{l.split(':', 1)[0]}: {request}"]
            head = lines[:i + (0 if rest else 1)]
            return "\n".join(head + body + [f"MARKS: {marks or 'none'}"])
    return transcript


# --- drawing ---------------------------------------------------------------------------------------------------------
def _font(kind: str, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONT_DIR / FONTS[kind]), size)


def render(transcript: str, ext: str, seed: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter

    rnd = random.Random(seed)
    lines, marks = split_marks(transcript)
    title = lines[0].strip() if lines else ""
    body = lines[1:]
    stamps = [w for w in STAMP_WORDS if re.search(rf"\b{w}\b", title)]
    for w in stamps:
        title = re.sub(rf"\s*\b{w}\b\s*", " ", title).strip()
    for w in STAMP_WORDS:
        if re.search(rf"\b{w}\b", marks) and w not in stamps and re.search(r"stamp", marks, re.I):
            stamps.append(w)
    struck = [s for s in re.findall(r"[\"'“‘]([^\"'”’]{3,})[\"'”’]", marks)
              if re.search(r"strike|struck|crossed", marks, re.I)]
    notes = [s for s in re.findall(r"[\"'“‘]([^\"'”’]{2,})[\"'”’]", marks)
             if re.search(r"handwrit", marks, re.I) and not any(s in l for l in lines)]

    W = 1100
    f_title, f_mono, f_hand, f_stamp = _font("title", 32), _font("mono", 23), _font("hand", 27), _font("stamp", 34)
    rows: list[tuple[str, object, str]] = []  # (text, font, colour)
    for raw in body:
        if not raw.strip():
            rows.append(("", f_mono, "ink"))
            continue
        hand = bool(re.match(r"\s*(signed|signature|approved by|initials)\b", raw, re.I)) or rnd.random() < 0.3
        width = 58 if hand else 66
        for piece in textwrap.wrap(raw, width) or [raw]:
            rows.append((piece, f_hand if hand else f_mono, "ink"))
    for n in notes:
        rows.append(("", f_mono, "ink"))
        rows.append((n, f_hand, "red"))
    line_h = 40
    H = 170 + line_h * len(rows) + 80
    paper = Image.new("RGB", (W, H), (238, 236, 230))
    d = ImageDraw.Draw(paper)
    for _ in range(int(W * H / 900)):  # paper grain
        x, y = rnd.randrange(W), rnd.randrange(H)
        g = rnd.randrange(215, 245)
        d.point((x, y), fill=(g, g, g - 4))
    d.text((80, 60), title, font=f_title, fill=(25, 25, 25))
    d.line((80, 112, W - 80, 112), fill=(90, 90, 90), width=2)
    for i, w in enumerate(stamps):
        x0, y0 = W - 300, 40 + i * 70
        d.rectangle((x0, y0, x0 + 230, y0 + 60), fill=(205, 205, 205))
        d.text((x0 + 20, y0 + 8), w, font=f_stamp, fill=(70, 70, 70))
    y = 140
    for text, font, colour in rows:
        fill = (150, 30, 30) if colour == "red" else (30, 30, 35)
        d.text((80 + rnd.randint(-2, 2), y), text, font=font, fill=fill)
        if text and any(s.strip() and s.strip() in text for s in struck):
            tw = d.textlength(text, font=font)
            d.line((78, y + 16, 82 + tw, y + 16), fill=(20, 20, 20), width=3)
        y += line_h
    paper = paper.rotate(rnd.uniform(-2.5, 2.5), resample=Image.BICUBIC, expand=True, fillcolor=(60, 58, 55))
    canvas = Image.new("RGB", (paper.width + 60, paper.height + 60), (60, 58, 55))
    canvas.paste(paper, (30, 30))
    canvas = canvas.filter(ImageFilter.GaussianBlur(0.4))
    out = io.BytesIO()
    if ext == "png":
        canvas.save(out, "PNG", optimize=True)
    else:
        canvas.save(out, "JPEG", quality=85)
    return out.getvalue()


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
