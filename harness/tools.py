"""Tool router (SPEC §3): `tree`, `list`, `read`, `search`, `query_logs` are answered from the workspace index; every
other tool (`threat_intel`, `access_path`, `get_containment`, state-changing tools) goes to the server. After a call
that may change files, the task's manifest is re-read and changed files are indexed."""
from __future__ import annotations

from arena.client import READ_ONLY_TOOLS, ArenaClient, ArenaError

from . import localtools
from .index import Index


class Tools:
    def __init__(self, client: ArenaClient, run_id: str, index: Index | None):
        self.client, self.run_id, self.index = client, run_id, index
        self.stats = {"local": 0, "server": 0}

    def read_text(self, task_id: str, path: str) -> str | None:
        """Full text of a workspace file (text formats as stored; office/pdf as their evidence units, one per line),
        or None if the task has no such file."""
        if self.index is not None and task_id in self.index.files:
            view = self.index.view(task_id)
            p = "/" + path.lstrip("/")
            d = view.doc(p)
            if d is None:
                return None
            if d.lines is not None:
                return "\n".join(d.lines)
            return "\n".join(u.text for u in view.units(p)[1:])
        try:
            data, _ = self.client.download_file(self.run_id, task_id, path)
        except ArenaError as e:
            if e.status == 404:
                return None
            raise
        return data.decode("utf-8", errors="replace")

    def call(self, task_id: str, tool: str, args: dict | None = None) -> dict:
        args = dict(args or {})
        if self.index is not None and tool in localtools.LOCAL_TOOLS and task_id in self.index.files:
            self.stats["local"] += 1
            return localtools.call(self.index.view(task_id), tool, args)
        self.stats["server"] += 1
        res = self.client.call_tool(self.run_id, task_id, tool, args)
        if tool not in READ_ONLY_TOOLS and self.index is not None:
            self.index.refresh(task_id)
        return res
