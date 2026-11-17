"""Offline checks of lab intake (build ticket 05): python tests/test_lab_intake.py

Request resolution (open text ticket, closed tickets, photo tickets, inbox), trusted rules, Jev answer → family, kind
and allowlist, and the INTAKE state filling the task record. No model calls (Jev is faked).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "harvester"))

import harness.intake as I  # noqa: E402
from arena import llm  # noqa: E402
from harness.machine import HANDLERS, Ctx, Deadlines, run_task  # noqa: E402
from harness.state import State, TaskRecord  # noqa: E402
from test_lab_machine import FakeClient  # noqa: E402

TICKET = "---\nticket: IR-1\nstatus: {s}\nassignee: ir-agent\n---\n\n# IR-1\n\n{body}\n"
AGENTS = ("# AGENTS.md\n\n## Instructions\nThe task instruction, the operator files /AGENTS.md and /runbook.md, and the "
          "policy files define your instructions. See also /notes/x.md for data.\n\n## Hard rules\n- never leak.\n")


def choice(c: str, p: float) -> dict:
    return {"type": "choice", "choice": c, "probabilities": {c: p}}


def test_resolve_open_text_ticket() -> None:
    files = {"/tickets/IR-1.md": TICKET.format(s="closed", body="Old request."),
             "/tickets/IR-2.md": TICKET.format(s="open", body="List the compromised agents as a JSON array."),
             "/tickets/IR-3.jpg": ""}
    r = I.resolve_request("Work the open ticket in /tickets/ and submit what it asks for.", list(files), files.get)
    assert r.via == "ticket" and r.source == "/tickets/IR-2.md" and r.text.startswith("# IR-1"), r
    assert "JSON array" in r.text and "status" not in r.text


def test_photo_tickets_wait_for_transcription() -> None:
    files = {"/tickets/IR-1.md": TICKET.format(s="closed", body="x"), "/tickets/IR-2.jpg": "", "/tickets/IR-3.jpg": ""}
    r = I.resolve_request("Your task is the open ticket in /tickets/.", list(files), files.get)
    assert r.text is None and r.unread_images == ["/tickets/IR-2.jpg", "/tickets/IR-3.jpg"], r
    seen = []

    def transcribe(p):
        seen.append(p)
        return "IR-3\nSTATUS: OPEN\nWhich agents have the lethal trifecta?" if p.endswith("3.jpg") else "STATUS: CLOSED"
    r = I.resolve_request("Your task is the open ticket in /tickets/.", list(files), files.get, transcribe)
    assert r.source == "/tickets/IR-3.jpg" and "trifecta" in r.text and seen == ["/tickets/IR-2.jpg", "/tickets/IR-3.jpg"]
    assert I.classify(I.Request(text=None), lambda s, q: 1 / 0).family == "other"  # no text → no Jev call


def test_inbox_request() -> None:
    files = {"/inbox/msg.md": '---\nfrom: "A" <a@x.example>\n---\n\nPlease block evil.test today.\n'}
    r = I.resolve_request("Handle the message in /inbox/ according to the rules.", list(files), files.get)
    assert r.via == "inbox" and r.messages == files and r.source == "/inbox/msg.md"
    st = I.jev_state(r)
    assert st["inbox"] == ["Please block evil.test today."] and "Handle the message" in st["request"]
    assert set(I.questions(True)) == {"action"}  # inbox: only the action question


def test_trusted_rules() -> None:
    assert I.operator_files(AGENTS) == ["/AGENTS.md", "/runbook.md"]  # /notes/x.md is not called an operator file
    files = {"/AGENTS.md": AGENTS, "/runbook.md": "defs"}
    rules, ops = I.trusted_rules(list(files), files.get)
    assert rules == AGENTS and ops == ["/AGENTS.md", "/runbook.md"]
    long = "# A\n\n## Background\n" + "noise " * 2000 + "\n## Hard rules\n- never leak.\n## Outcomes\n- OK.\n"
    c = I.condense_rules(long)
    assert len(c) <= I.RULES_CHARS and "## Hard rules\n- never leak." in c and "## Outcomes" in c and "noise" not in c
    assert I.trusted_rules(["/x.md"], files.get) == ("", [])


def test_interpret() -> None:
    it = I.interpret({"family": choice("containment", 0.97)}, False)
    assert (it.family, it.kind, it.confident) == ("containment", "containment", True)
    assert it.allowlist == ["containment:add_note", "containment:block_domain", "containment:quarantine_agent",
                            "containment:rotate_token"]
    assert I.interpret({"family": choice("rotation", 0.9)}, False).allowlist == ["containment:rotate_token"]
    assert I.interpret({"family": choice("partner_draft", 0.9)}, False).allowlist == ["write"]
    assert I.interpret({"family": choice("breach_notification", 0.9)}, False).allowlist == ["send_message"]
    assert I.interpret({"family": choice("compromised", 0.9)}, False).allowlist == []
    low = I.interpret({"family": choice("containment", 0.3)}, False)
    assert not low.confident and low.allowlist == []                     # unsure → loop, nothing allowed
    assert I.interpret({"family": {"type": "refusal"}}, False).family == "other"
    assert I.interpret({}, False).family == "other"
    ib = I.interpret({"action": choice("quarantine_agent", 0.95)}, True)
    assert (ib.family, ib.kind, ib.allowlist) == ("inbox", "action:quarantine_agent", ["containment:quarantine_agent"])
    none = I.interpret({"action": choice("none", 0.95)}, True)
    assert none.kind == "answer" and none.allowlist == []


def test_allowlists_match_the_grader_mapping() -> None:
    from replay.grade import AUTHORISED
    assert {k: set(v) for k, v in I.AUTHORISED.items()} == {k: set(v) for k, v in AUTHORISED.items()}


def test_intake_state_fills_the_record() -> None:
    files = {"/AGENTS.md": AGENTS, "/runbook.md": "defs",
             "/tickets/IR-2.md": TICKET.format(s="open", body='Contain it. Answer as JSON {"quarantine": []}.')}
    calls = []
    with tempfile.TemporaryDirectory() as d:
        c = Ctx(client=FakeClient(files=files), run_id="r1", out_dir=Path(d), tracker=llm.CostTracker(),
                deadlines=Deadlines())
        c.pacer.decide = lambda st, qs, **kw: calls.append((st, qs)) or {"family": choice("containment", 0.99)}
        rec = run_task(c, TaskRecord("t01", "Work the open ticket in /tickets/.", 2),
                       {**HANDLERS, State.SOLVE: lambda c, r: State.FALLBACK_SUBMIT})
        assert len(calls) == 1 and "Contain it" in calls[0][0]["request"]
        assert (rec.family, rec.task_kind, rec.intake_confident) == ("containment", "containment", True)
        assert rec.request_source == "/tickets/IR-2.md" and rec.answer_shape == "object"
        assert rec.rules == AGENTS and rec.operator_files == ["/AGENTS.md", "/runbook.md"]
        assert "containment:quarantine_agent" in rec.allowlist
        assert c.client.submits[0][2] == {}                                # right-shaped from the ticket's format
        assert TaskRecord.load(Path(d) / "state" / "t01.json").family == "containment"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("lab intake tests ok")
