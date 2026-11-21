"""Facts inside a run: one Judge per run on the pacer (Jev, Gemma for the uncertain band), and exactly the
transcripts the asked facts need, read through ticket 08's vision (cached per blob, attached to the index).

    f = facts_of(ctx, task_id)                          # trace facts + standing order
    f = facts_of(ctx, task_id, need=("settings",))      # only the signed standing order

`need`: "artifacts" (quarantined artifacts, their attachments, scanned attachment pages and embedded images: the
inputs of the instruction judgment, hence of every attack-trace fact) and/or "settings" (the signed standing order).
"""
from __future__ import annotations

import base64
import hashlib
import re
import threading
from typing import Any

from .. import vision
from .decode import pdf_page_image
from .instruction_judge import Judge
from .runbook import Facts, facts_for

_lock = threading.Lock()


def judge_of(ctx: Any) -> Judge:
    """The run's judge (verdicts cached by content hash for the whole run)."""
    with _lock:
        j = getattr(ctx, "_facts_judge", None)
        if j is None:
            j = Judge(decide=lambda state, qs, **kw: ctx.pacer.decide(state, qs),
                      chat=lambda msgs, **kw: ctx.pacer.chat(msgs, **kw))
            ctx._facts_judge = j
        return j


def needed(probe: Facts, need: tuple[str, ...]) -> list[str]:
    keys: list[str] = []
    if "artifacts" in need:
        keys += [k for a in probe.artifacts().value.values() for k in a["missing"]]
    if "settings" in need:
        m = re.search(r"signed\s+standing\s+order\s+\((/[^)\s,]+)", probe.text("/runbook.md") or "")
        if m:
            keys.append(m.group(1))
    return list(dict.fromkeys(keys))


def transcribe_key(ctx: Any, task_id: str, key: str, log=None) -> str:
    """An image path through vision; `<pdf>#page=<n>` and `<file>#image=<i>` by extracting the image first."""
    path, _, frag = key.partition("#")
    vis = vision.of(ctx)
    if not frag:
        return vis.transcribe(task_id, path, log) if vision.is_image(path) else ""
    sha = vis.sha(task_id, path)
    if not sha:
        return ""
    data = ctx.client.blob(ctx.run_id, sha, task_id)
    kind, _, n = frag.partition("=")
    img = None
    if kind == "page":
        img = pdf_page_image(data, int(n))
    elif kind == "image":
        uris = re.findall(r"data:(image/[a-z+.-]+);base64,([A-Za-z0-9+/=]+)", data.decode("utf-8", "replace"))
        if 0 < int(n) <= len(uris):
            img = (base64.b64decode(uris[int(n) - 1][1]), uris[int(n) - 1][0])
    if img is None:
        return ""
    h = hashlib.sha256(img[0]).hexdigest()
    with vis.lock:
        if h in vis.cache:
            return vis.cache[h]
    url = f"data:{img[1]};base64,{base64.b64encode(img[0]).decode()}"
    msg = ctx.pacer.chat([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                                       {"type": "text", "text": vision.PROMPT}]}],
                         max_tokens=vision.MAX_TOKENS, temperature=0.0, log=log)
    text = (msg.get("content") or "").strip()
    with vis.lock:
        vis.cache[h] = text
        vis.calls += 1
    if log:
        log({"event": "transcribe", "path": key, "sha": h[:12], "chars": len(text)})
    return text


def facts_of(ctx: Any, task_id: str, need: tuple[str, ...] = ("artifacts", "settings"), log=None) -> Facts:
    """The task's facts with the transcripts `need` asks for (none of them read twice in a run)."""
    if ctx.index is None or task_id not in ctx.index.files:
        raise RuntimeError("no workspace index for this task")
    view = ctx.index.view(task_id)
    judge = judge_of(ctx)
    probe = Facts(view, judge)
    transcripts = {}
    for k in needed(probe, need):
        t = transcribe_key(ctx, task_id, k, log)
        if t:
            transcripts[k] = t
    return facts_for(view, judge, transcripts)
