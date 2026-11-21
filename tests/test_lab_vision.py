"""Offline checks of images per need (build ticket 08): python tests/test_lab_vision.py

Stamp parsing, one read per blob (also under concurrency), transcripts as untrusted path-cited units in the index,
photo tickets in intake, images the request names, on-demand reads in the loop. Gemma is faked; no model calls.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lab_machine import FakeClient  # noqa: E402,I001  (stubs Pacer.decide / Pacer.chat)
from arena import llm  # noqa: E402
from harness import intake as I  # noqa: E402
from harness import loop as L  # noqa: E402
from harness import vision as V  # noqa: E402
from harness.index import Index  # noqa: E402
from harness.machine import HANDLERS, Ctx, Deadlines, run_task  # noqa: E402
from harness.state import State, TaskRecord  # noqa: E402
from harness.tool_router import Tools  # noqa: E402

TRANSCRIPTS = {
    "/tickets/IR-1.jpg": "Incident response ticket IR-1\nCLOSED\nList the tokens.\nMARKS: stamp CLOSED (red), signature",
    "/tickets/IR-2.jpg": "Incident response ticket IR-2 OPEN\nWhich domains on /intel/scans/p1.jpg did we contact? "
                         "Sorted JSON array.\nMARKS: stamp OPEN",
    "/tickets/IR-3.jpg": "Incident response ticket IR-3\nOPEN\nMARKS: stamp OPEN",
    "/intel/scans/p1.jpg": "SOC indicators page 1\nevil-mirror.test\nnot-a-phish.test\nMARKS: none",
}
FILES = {"/AGENTS.md": "# AGENTS\n\nThe operator files /AGENTS.md and /runbook.md define your instructions.\n",
         "/runbook.md": "# Runbook\n\nTonight's rules are on the signed standing order (/standing_order.jpg).\n",
         "/standing_order.jpg": "", **{p: "" for p in TRANSCRIPTS}}
TRANSCRIPTS["/standing_order.jpg"] = "STANDING ORDER\nRotation buffer: 30 minutes\nMARKS: signature"


class ImgClient(FakeClient):
    def blob(self, run_id, sha, task_id=None):
        return BY_SHA[sha]


BY_SHA: dict[str, bytes] = {}


def build() -> tuple[Index, dict[str, str]]:
    idx, files = Index(), {}
    for p, text in FILES.items():
        b = (text or f"image bytes of {p}").encode()
        h = hashlib.sha256(b).hexdigest()
        BY_SHA[h] = p.encode()  # the "image" carries its own path, so the fake model knows what to transcribe
        idx.add(p, h, b)
        files[p] = h
    idx.files["t01"] = files
    return idx, files


def ctx(tmp: Path, delay: float = 0.0) -> Ctx:
    c = Ctx(client=ImgClient(files=FILES), run_id="r1", out_dir=tmp, tracker=llm.CostTracker(), deadlines=Deadlines())
    c.index, _ = build()
    c.tools = Tools(c.client, "r1", c.index)
    c.calls = []  # type: ignore[attr-defined]

    def chat(messages, **kw):
        parts = messages[-1]["content"]
        if isinstance(parts, list):  # a vision call: the fake image's bytes name its path
            path = __import__("base64").b64decode(parts[0]["image_url"]["url"].split(",", 1)[1]).decode()
            c.calls.append(path)
            time.sleep(delay)
            return {"content": TRANSCRIPTS[path], "_usage": {"prompt_tokens": 298}}
        return {"content": '{"outcome": "OK", "answer": [], "cite": []}', "tool_calls": None, "_usage": {}}
    c.pacer.chat = chat
    return c


def test_stamps() -> None:
    assert V.stamp_status(TRANSCRIPTS["/tickets/IR-1.jpg"]) == "closed"
    assert V.stamp_status(TRANSCRIPTS["/tickets/IR-2.jpg"]) == "open"
    assert V.stamp_status("Ticket IR-9\nstatus: closed\nPlease open the file.\nMARKS: none") == "closed"
    assert V.stamp_status("Ticket\nsome text\nMARKS: none") is None
    assert V.stamp_status("Ticket IR-4\nnothing\nMARKS: stamp OPEN over a struck-through CLOSED") is None  # ambiguous
    assert V.body(TRANSCRIPTS["/tickets/IR-2.jpg"]).endswith("Sorted JSON array.")
    assert V.marks(TRANSCRIPTS["/tickets/IR-1.jpg"]) == "stamp CLOSED (red), signature"


def test_one_read_per_blob_and_units_in_the_index() -> None:
    with tempfile.TemporaryDirectory() as d:
        c = ctx(Path(d), delay=0.05)
        ts = [threading.Thread(target=V.of(c).transcribe, args=("t01", "/intel/scans/p1.jpg")) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert c.calls == ["/intel/scans/p1.jpg"], c.calls          # one call although 8 threads asked at once
        view = c.index.view("t01")
        units = [u for u in view.units("/intel/scans/p1.jpg") if "image-transcript" in u.why]
        assert [u.text for u in units] == ["SOC indicators page 1", "evil-mirror.test", "not-a-phish.test",
                                           "MARKS: none"]
        assert all(u.untrusted and u.loc == {} and u.source() == {"path": "/intel/scans/p1.jpg"} for u in units)
        hit = view.search("evil-mirror")[0][1]
        assert hit.path == "/intel/scans/p1.jpg" and hit.text == "evil-mirror.test"
        assert view.find({"path": "/intel/scans/p1.jpg"}) is not None
        assert c.tools.read_text("t01", "/intel/scans/p1.jpg").startswith("SOC indicators")
        assert V.of(c).transcribe("t01", "/nope.jpg") == "" and V.of(c).calls == 1


def test_intake_reads_photo_tickets_until_open_and_named_images() -> None:
    with tempfile.TemporaryDirectory() as d:
        c = ctx(Path(d))
        rec = run_task(c, TaskRecord("t01", "Work the open ticket in /tickets/.", 2),
                       {**HANDLERS, State.SOLVE: lambda c, r: State.FALLBACK_SUBMIT})
        # IR-1 closed, IR-2 open: IR-3 is never read; the scan IR-2 names is read at intake
        assert c.calls == ["/tickets/IR-1.jpg", "/tickets/IR-2.jpg", "/intel/scans/p1.jpg"], c.calls
        assert rec.request_source == "/tickets/IR-2.jpg" and "Sorted JSON array" in rec.request
        assert "MARKS" not in rec.request and rec.answer_shape == "list" and not rec.unread_images


def test_loop_reads_operator_images_once_and_images_on_demand() -> None:
    with tempfile.TemporaryDirectory() as d:
        c = ctx(Path(d))
        rec = TaskRecord("t01", "x", 1, request="Which tokens leaked?", operator_files=["/AGENTS.md", "/runbook.md"],
                         state=State.LOOP)
        inner = c.pacer.chat
        steps = iter([{"content": None, "_usage": {}, "tool_calls": [{"id": "1", "type": "function", "function": {
            "name": "read", "arguments": '{"path": "/tickets/IR-3.jpg"}'}}]}])

        def chat(messages, **kw):
            if kw.get("tools"):
                return next(steps, {"content": None, "_usage": {}, "tool_calls": [
                    {"id": "2", "type": "function", "function": {"name": "done", "arguments": "{}"}}]})
            return inner(messages, **kw)
        c.pacer.chat = chat
        run_task(c, rec)
        assert c.calls == ["/standing_order.jpg", "/tickets/IR-3.jpg"], c.calls
        rec2 = TaskRecord("t02", "x", 1, request="y", operator_files=["/runbook.md"])
        c.index.files["t02"] = c.index.files["t01"]
        L.step(c, rec2)  # another task: the standing order is cached for the run
        assert c.calls.count("/standing_order.jpg") == 1


def test_photo_tickets_without_vision_stay_unread() -> None:
    files = {"/tickets/IR-1.jpg": ""}
    r = I.resolve_request("Your task is the open ticket in /tickets/.", list(files), files.get)
    assert r.text is None and r.unread_images == ["/tickets/IR-1.jpg"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("lab vision tests ok")
