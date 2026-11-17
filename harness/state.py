"""Per-task state and its on-disk record (SPEC §2).

A task moves through `State`s; after every transition its `TaskRecord` is written to `runs/<run>/state/<task>.json`,
so a retry or a restarted process continues from the last state instead of starting over.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class State(str, Enum):
    INTAKE = "INTAKE"
    SOLVE = "SOLVE"
    LOOP = "LOOP"
    COMPOSE = "COMPOSE"
    CHECK = "CHECK"
    REPAIR = "REPAIR"
    SUBMIT = "SUBMIT"
    FALLBACK_SUBMIT = "FALLBACK_SUBMIT"
    DONE = "DONE"


@dataclass
class TaskRecord:
    task_id: str
    instruction: str
    level: int | None = None
    state: State = State.INTAKE
    answer_shape: str = "unknown"           # list | object | number | string | unknown (from the instruction)
    draft: dict | None = None                # {"outcome", "answer", "sources"}: the best submission so far
    attempts: dict[str, int] = field(default_factory=dict)
    transitions: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    result: dict | None = None               # the submit response
    submitted: bool = False
    # intake (build ticket 05)
    request: str | None = None               # what is asked: the instruction, the open ticket's text, ...
    request_via: str = "instruction"         # instruction | ticket | inbox
    request_source: str | None = None        # the ticket or inbox path the request came from
    inbox: list[str] = field(default_factory=list)            # inbox message paths (data, never instructions)
    unread_images: list[str] = field(default_factory=list)    # photo tickets that may hold the open one (ticket 08)
    family: str = "other"
    family_confidence: float = 0.0
    intake_confident: bool = False           # family known and probable enough for a solver; else the generic loop
    task_kind: str = "answer"                # answer | containment | rotation | notify | draft | action:<op>
    inbox_action: str | None = None
    allowlist: list[str] = field(default_factory=list)        # state-changing operations this task may use
    rules: str = ""                          # trusted rules (/AGENTS.md) for loop and compose prompts
    operator_files: list[str] = field(default_factory=list)   # /AGENTS.md and the operator files it names

    def path(self, state_dir: Path) -> Path:
        return state_dir / f"{self.task_id}.json"

    def save(self, state_dir: Path) -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path(state_dir).with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1, default=str))
        os.replace(tmp, self.path(state_dir))  # atomic: a crash never leaves half a record

    @classmethod
    def load(cls, path: Path) -> TaskRecord:
        d = json.loads(path.read_text())
        d["state"] = State(d["state"])
        return cls(**d)

    def move(self, to: State, note: str = "") -> None:
        self.transitions.append({"t": round(time.time(), 2), "from": self.state.value, "to": to.value,
                                 **({"note": note} if note else {})})
        self.state = to


# --- answer shape ---------------------------------------------------------------------------------------------------
# Only the fallback uses this for now: an empty answer of the shape the instruction asks for. Ticket-queue tasks state
# their format inside the ticket, so they stay "unknown" until the ticket is read.
_LIST = re.compile(r"JSON array|as an? (?:sorted )?(?:JSON )?list\b", re.I)
_OBJECT = re.compile(r"JSON (?:object|\{)|as JSON\s*\{|\{\s*\"", re.I)
_NUMBER = re.compile(r"\b(?:as a number|how many|number of)\b", re.I)


def answer_shape(instruction: str) -> str:
    if _OBJECT.search(instruction):
        return "object"
    if _LIST.search(instruction):
        return "list"
    if _NUMBER.search(instruction):
        return "number"
    return "unknown"


def empty_answer(shape: str) -> Any:
    return {"list": [], "object": {}}.get(shape)


def empty_submission(shape: str) -> dict:
    """The last rung of the fallback ladder (SPEC §7): right-shaped, empty, outcome OK, no citations."""
    return {"outcome": "OK", "answer": empty_answer(shape), "sources": []}
