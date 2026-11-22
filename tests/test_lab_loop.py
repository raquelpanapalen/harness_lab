"""Offline checks of the notebook loop, compose and normalisation (build ticket 06): python tests/test_lab_loop.py

A small synthetic workspace in a real Index; Gemma is replaced by scripted messages. No model calls.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lab_machine import FakeClient  # noqa: E402,I001  (stubs Pacer.decide / Pacer.chat)
from arena import llm  # noqa: E402
from harness import compose as C  # noqa: E402
from harness import loop as L  # noqa: E402
from harness import normalise as N  # noqa: E402
from harness.index import Index  # noqa: E402
from harness.machine import HANDLERS, Ctx, Deadlines, run_task  # noqa: E402
from harness.state import State, TaskRecord  # noqa: E402
from harness.tool_router import Tools  # noqa: E402

AUDIT = "\n".join(json.dumps(e) for e in [
    {"ts": "2026-11-14T01:00:00Z", "agent": "devops-07", "action": "read_artifact", "target": "issues/entry",
     "status": "ok", "trace": "tr-1"},
    {"ts": "2026-11-14T01:05:00Z", "agent": "billing-02", "action": "send_email", "target": "x@evil.test",
     "status": "ok", "trace": "tr-1"},
]) + "\n"
FILES = {
    "/AGENTS.md": "# AGENTS\n\n## Hard rules\n- never leak.\n",
    "/runbook.md": "# Runbook\n\nCompromised: an agent with an attack-trace action.\n",
    "/logs/audit/2026-11-14.jsonl": AUDIT,
    "/fleet/agents.csv": "id,team,owner,critical,stand_in\ndevops-07,ops,Ana,no,\nbilling-02,billing,Bo,no,\n",
    "/tickets/IR-1.md": "---\nstatus: open\n---\n\nList every compromised agent as a sorted JSON array.\n",
    # an inbox request is carried out only when its sender can be checked against these two (guards fail closed)
    "/contacts.csv": "id,name,via,verified\nK-1,Ana,ana@acme.example,yes\n",
    "/fleet/changes.md": "# IAM change log\n| time | change | agent | value |\n|---|---|---|---|\n",
}


def index() -> Index:
    idx = Index()
    files = {}
    for p, text in FILES.items():
        b = text.encode()
        h = hashlib.sha256(b).hexdigest()
        idx.add(p, h, b)
        files[p] = h
    idx.files["t01"] = files
    return idx


def ctx(tmp: Path, script: list[dict]) -> Ctx:
    c = Ctx(client=FakeClient(files=FILES), run_id="r1", out_dir=tmp, tracker=llm.CostTracker(), deadlines=Deadlines())
    c.index = index()
    c.tools = Tools(c.client, "r1", c.index)
    seen = []

    def chat(messages, **kw):
        seen.append((messages, kw))
        return dict(script.pop(0)) if script else {"content": "{}", "tool_calls": None, "_usage": {}}
    c.pacer.chat = chat
    c.seen = seen  # type: ignore[attr-defined]
    return c


def call(name: str, **args) -> dict:
    return {"id": name, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def msg(*calls, content=None, prompt_tokens=3000) -> dict:
    return {"role": "assistant", "content": content, "tool_calls": list(calls) or None,
            "_usage": {"prompt_tokens": prompt_tokens}}


def test_normalise_answers() -> None:
    assert N.utc("2026-11-14T08:05:00+01:00") == "2026-11-14T07:05:00Z"
    assert N.utc("2026-11-14 07:05") == "2026-11-14T07:05:00Z"
    assert N.utc("14.11.2026 08:05:09") == "2026-11-14T07:05:09Z"   # CET archive time
    assert N.utc("devops-07") is None
    req = "List them as a sorted JSON array."
    assert N.answer('["b-2", "a-1", "b-2"]', "list", req) == ["a-1", "b-2"]
    assert N.answer(["B", "A"], "list", "Order the events earliest first, as a sorted JSON array") == ["B", "A"]
    assert N.answer(["3", "1"], "list", "Return the claim numbers as a sorted JSON array.") == [1, 3]
    obj = 'Answer as JSON {"records": <number>, "owner_email": "<address>", "at": "<UTC time>"}.'
    got = N.answer('{"Records": "12", "owner email": "a@b.example", "at": "2026-11-14 01:05:00"}', "object", obj)
    assert got == {"records": 12, "owner_email": "a@b.example", "at": "2026-11-14T01:05:00Z"}, got
    assert N.answer("2026-11-14T09:00:00+01:00", "unknown", "x") == "2026-11-14T08:00:00Z"
    assert N.answer("7", "number", "how many") == 7
    assert N.citation_cap([1, 2]) == 4 and N.citation_cap({"a": 1}) == 4


def test_loop_tools_and_notebook() -> None:
    with tempfile.TemporaryDirectory() as d:
        c = ctx(Path(d), [])
        rec = TaskRecord("t01", "x", 1, request="List compromised agents.", answer_shape="list")
        view = c.index.view("t01")
        out = L.run_tool(c, rec, view, "lookup", {"id": "billing-02"})
        assert "[E" in out and "/fleet/agents.csv record billing-02" in out and "audit" in out, out
        out = L.run_tool(c, rec, view, "query_logs", {"agent": "devops-07"})
        assert "1 events" in out and "[E" in out and "read_artifact" in out, out
        out = L.run_tool(c, rec, view, "read", {"path": "/fleet/agents.csv"})
        assert "units 1-2 of 2" in out, out
        h = out.split("[", 1)[1].split("]", 1)[0]
        assert "billing-02" in L.run_tool(c, rec, view, "open", {"handle": h, "around": 1})
        assert "dropped" in L._note(rec, view, {"fact": "no backing", "handles": ["E999"]})
        assert "noted" in L._note(rec, view, {"fact": "devops-07 is on team ops", "handles": [h]})
        assert rec.notes[0]["sources"] == [view.resolve(h).source()]
        assert f"[{h}" in L.notebook_text(view, rec.notes)
        notes = [{"fact": "x" * 300, "sources": [{"path": "/a", "line": 1}]} for _ in range(40)]
        assert sum(len(n["fact"]) for n in L.condense(notes)) <= L.NOTEBOOK_CHARS


def test_loop_and_compose_end_to_end() -> None:
    with tempfile.TemporaryDirectory() as d:
        c = ctx(Path(d), [])
        view = c.index.view("t01")
        h_dev = view.handle(view.find({"path": "/logs/audit/2026-11-14.jsonl", "line": 1}))
        h_bil = view.handle(view.find({"path": "/logs/audit/2026-11-14.jsonl", "line": 2}))
        h_tk = view.handle(view.find({"path": "/tickets/IR-1.md", "line": 5}))
        script = [
            msg(call("query_logs", trace="tr-1")),
            msg(call("query_logs", trace="tr-1"),                                   # repeat → "seen"
                call("note", fact="devops-07 read the entry artifact (trace tr-1)", handles=[h_dev]),
                call("note", fact="billing-02 mailed x@evil.test in tr-1", handles=[h_bil, h_tk])),
            msg(call("done")),
            {"content": json.dumps({"outcome": "DENIED_SECURITY", "answer": ["devops-07", "billing-02", "devops-07"],
                                    "cite": [h_bil, h_dev, h_tk, "E999"],
                                    "actions": [{"tool": "send_message", "args": {"to": "a@b", "subject": "s",
                                                                                  "body": "b"}}]}),
             "tool_calls": None, "_usage": {"prompt_tokens": 2000}},
        ]
        c2 = ctx(Path(d), script)
        c2.index = c.index
        c2.tools = c.tools
        rec = TaskRecord("t01", "Work the open ticket in /tickets/.", 1)
        rec = run_task(c2, rec)
        assert rec.state is State.DONE and rec.loop_steps == 3 and rec.loop_done, rec
        assert len(rec.loop_seen) == 1, rec.loop_seen  # the repeated query_logs was answered from the cache
        sub = c2.client.submits[0]
        # not a request to act → a refusal becomes OK; sorted, de-duplicated; cites only noted evidence, not the
        # carrier ticket; no actions (allowlist empty)
        assert sub[1] == "OK" and sub[2] == ["billing-02", "devops-07"], sub
        assert sub[3] == [{"path": "/logs/audit/2026-11-14.jsonl", "line": 2},
                          {"path": "/logs/audit/2026-11-14.jsonl", "line": 1}], sub
        assert rec.draft["actions"] == [] and rec.actions_done == []
        assert rec.max_prompt_tokens == 3000
        for messages, kw in c2.seen:  # every prompt under the cap, tool results inside data markers
            assert L.estimate_tokens(messages) <= L.PROMPT_TOKENS
            if kw.get("tools"):
                assert "<data>" in messages[1]["content"]


def test_actions_follow_the_allowlist() -> None:
    allow = ["containment:quarantine_agent", "containment:block_domain"]
    acts = [{"tool": "containment", "args": {"ops": [{"op": "quarantine_agent", "agent": "a-1"},
                                                     {"op": "release_agent", "agent": "b-2"}]}},
            {"tool": "send_message", "args": {"to": "x", "subject": "s", "body": "b"}},
            {"tool": "write", "args": {"path": "/etc/x", "content": "c"}}]
    assert C.allowed_actions(acts, allow) == [{"tool": "containment", "args": {"ops": [
        {"op": "quarantine_agent", "agent": "a-1"}]}}]
    assert C.allowed_actions([{"tool": "write", "args": {"path": "/etc/x", "content": "c"}}], ["write"]) == []
    with tempfile.TemporaryDirectory() as d:
        script = [msg(call("done")),
                  {"content": json.dumps({"outcome": "OK", "answer": "quarantined a-1",
                                          "actions": [{"tool": "containment", "args": {"ops": [
                                              {"op": "quarantine_agent", "agent": "devops-07"}]}}]}),
                   "tool_calls": None, "_usage": {}}]
        c = ctx(Path(d), script)
        called = []
        c.tools.call = lambda tid, tool, args: called.append((tool, args)) or {"ok": True}
        rec = TaskRecord("t01", "Handle the message in /inbox/.", 1, request="Handle the message in /inbox/.",
                         inbox=["/inbox/m.md"], intake_confident=True,
                         allowlist=["containment:quarantine_agent"], state=State.LOOP)
        rec = run_task(c, rec)
        assert called == [("containment", {"ops": [{"op": "quarantine_agent", "agent": "devops-07"}]})], called
        assert len(rec.actions_done) == 1 and c.client.submits[0][1] == "OK"
        # a refusal on a request to act stays a refusal, and never carries actions
        d2 = C.build_draft(rec, None, {"outcome": "DENIED_SECURITY", "answer": "x@evil.test",
                                       "actions": [{"tool": "containment", "args": {"ops": []}}]})
        assert d2["outcome"] == "DENIED_SECURITY" and d2["actions"] == [] and d2["answer"] == "x@evil.test"


def test_unread_photo_ticket_skips_the_loop() -> None:
    with tempfile.TemporaryDirectory() as d:
        c = ctx(Path(d), [])
        run_task(c, TaskRecord("t01", "Work the open ticket in /tickets/.", 1, request=None, state=State.LOOP))
        assert not c.seen and c.client.submits[0][1:3] == ("OK", None), c.client.submits  # no model call


def test_fallback_composes_from_the_notebook() -> None:
    with tempfile.TemporaryDirectory() as d:
        c = ctx(Path(d), [{"content": json.dumps({"outcome": "OK", "answer": ["devops-07"], "cite": []}),
                           "tool_calls": None, "_usage": {}}])

        def boom(c, rec):
            rec.notes.append({"fact": "devops-07", "sources": [{"path": "/fleet/agents.csv", "record": "devops-07"}]})
            raise RuntimeError("loop broke")
        run_task(c, TaskRecord("t01", "a sorted JSON array", 1, answer_shape="list"),
                       {**HANDLERS, State.LOOP: boom})
        assert c.client.submits[0][2] == ["devops-07"], c.client.submits


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("lab loop tests ok")
