"""Offline: a submit rejected with 400 (here: a source the server refuses) goes back to the model, which can fix it.
(Sources without `path` never reach the server any more: agent.py drops them before submitting.)
python tests/test_submit_retry.py"""
import json
import pathlib
import sys

KIT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))
import agent  # noqa: E402
from arena import llm  # noqa: E402
from arena.client import ArenaError, Task  # noqa: E402

submits = [
    {"outcome": "OK", "answer": ["a"], "sources": [{"path": "/a.csv", "record": "a"}, {"path": "/b.csv", "record": "b"}]},
    {"outcome": "OK", "answer": ["a"], "sources": [{"path": "/a.csv", "record": "a"}]},
]
seen: list[list] = []


def fake_chat(messages, **kw):
    i = len(seen)
    return {"content": "", "tool_calls": [{"id": f"c{i}", "type": "function",
            "function": {"name": "submit", "arguments": json.dumps(submits[min(i, 1)])}}]}


class FakeClient:
    def submit(self, run_id, task_id, outcome, answer, sources):
        seen.append(sources)
        if any(s.get("path") == "/b.csv" for s in sources):
            raise ArenaError("POST /submit -> 400: invalid request: sources.1: unknown file", status=400)
        return {"received": True, "task_id": task_id, "score": 1}


llm.chat = fake_chat
res = agent.solve_task(FakeClient(), "run_x", Task(task_id="t01", instruction="x"), [], agent.Config(max_steps=5),
                       llm.CostTracker())
assert res.get("received") is True, res
assert len(seen) == 2, seen
print("ok: a 400 on submit goes back to the model, the second submit lands")
