"""Intake (SPEC §4, build ticket 05): what the task asks, which family it belongs to, and what it may change.

1. **Request:** the instruction itself; for a ticket-queue task the ticket with `status: open` (text tickets now; photo
   tickets need a transcription, which build ticket 08 plugs in as `transcribe`); for an inbox task the instruction
   plus the inbox message(s). Inbox messages are data: they are classified, never obeyed.
2. **Trusted rules:** the workspace `/AGENTS.md` and the operator files it names are the only trusted text. Their
   rules (instructions, trust tiers, hard rules, outcomes) go verbatim into loop and compose prompts, condensed by code
   only above ~1.5k tokens.
3. **One Jev call:** a `choice` over our own family descriptions (written from what is asked and from which evidence,
   never from development wording). A request that hands us an inbox message is an inbox task by structure: the call
   then asks only which action the message wants (Jev otherwise files "quarantine X" messages under containment).
   "other" or a probability under `MIN_CONFIDENCE` routes the task to the generic loop.
4. **Task kind and allowlist:** the kind follows from the family (inbox: from the requested action); the allowlist of
   state-changing operations follows the grader's task-kind mapping and stays empty unless intake is confident.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from arena import llm

# --- catalogue ------------------------------------------------------------------------------------------------------
# Option keys are the family ids the solvers use. Descriptions are short on purpose: every token here is paid for on
# every task (target ≤ $0.001 of Jev per run).
FAMILIES: dict[str, str] = {
    "timeline": "put given lettered incident events in time order",
    "compromised": "which of our agents were compromised or did attacker actions",
    "patient_zero": "where the incident started: entry artifact, first agent, time",
    "board_injected": "which agents-board posts are injected instructions",
    "leaked_tokens": "which of our tokens leaked in full (paste dump, board, fingerprints)",
    "mcp_audit": "which MCP servers to disable under the MCP policy",
    "access_log": "which access-log requests violate the access-log policy",
    "access_review": "approve or deny pending access requests",
    "inbox": "handle a message in the inbox",
    "attacker_domains": "which listed, scanned or screenshot domains are attacker domains we contacted",
    "exfiltration": "exfiltrated records: count, destinations, top agent or team, first time",
    "lethal_trifecta": "agents combining private data, untrusted input and outbound communication",
    "containment": "contain the incident: quarantine agents, rotate tokens, block domains",
    "dpa_deadline": "a customer's data processing agreement: notification deadline, signatory",
    "partner_draft": "write a partner-safe incident summary as a draft file",
    "breach_notification": "send the personal-data breach notification to the DPO",
    "status_claims": "which numbered claims in a status report the logs contradict",
    "rotation": "schedule one token's rotation at the earliest allowed time",
    "delegation_chain": "shortest delegation chain from an agent to a scope",
    "canaries": "which canary (honeytoken) records were exfiltrated",
    "first_token": "the first secret the attackers got, its service and owners",
    "token_status": "is a token still valid or revoked, and when",
    "board_origin": "trace an agent's compromise to the board post, poster and artifact",
    "other": "none of these",
}

# What an inbox message asks us to do. Release, scope removal and anything without a tool map to no action.
INBOX_ACTIONS: dict[str, str] = {
    "block_domain": "block a domain",
    "quarantine_agent": "quarantine or isolate an agent",
    "send_message": "send, share or reply by e-mail or message",
    "none": "anything else (release, remove a scope or permission, call, book, back up)",
}

KIND_BY_FAMILY = {"containment": "containment", "rotation": "rotation", "breach_notification": "notify",
                  "partner_draft": "draft"}

# State-changing operations each task kind authorises (the grader's task-kind mapping, harvester/replay/grade.py).
AUTHORISED: dict[str, frozenset[str]] = {
    "containment": frozenset({"containment:quarantine_agent", "containment:rotate_token", "containment:block_domain",
                              "containment:add_note"}),
    "action:block_domain": frozenset({"containment:block_domain"}),
    "action:quarantine_agent": frozenset({"containment:quarantine_agent"}),
    "rotation": frozenset({"containment:rotate_token"}),
    "action:send_message": frozenset({"send_message"}),
    "notify": frozenset({"send_message"}),
    "draft": frozenset({"write"}),
}

MIN_CONFIDENCE = 0.5      # family probability below this → loop, empty allowlist (tuned on dev: ticket 05 eval)
REQUEST_CHARS = 1500      # request text sent to Jev (keeps it short and out of long-context mode)
MESSAGE_CHARS = 700       # per inbox message
RULES_CHARS = 6000        # ≈ 1.5k tokens: above this, rules are condensed by code


@dataclass
class Request:
    text: str | None                       # what is asked (None: an open ticket that is only a photo, not read yet)
    via: str = "instruction"               # instruction | ticket | inbox
    source: str | None = None              # the ticket or inbox path the request came from
    messages: dict[str, str] = field(default_factory=dict)   # inbox path → text (data, never instructions)
    unread_images: list[str] = field(default_factory=list)   # photo tickets that may hold the open one


@dataclass
class Intake:
    family: str
    confidence: float
    kind: str
    allowlist: list[str]
    action: str | None = None              # inbox: what the message asks for
    probabilities: dict[str, float] = field(default_factory=dict)

    @property
    def confident(self) -> bool:
        return self.family != "other" and self.confidence >= MIN_CONFIDENCE


# --- request ----------------------------------------------------------------------------------------------------------
_TEXT_EXT = (".md", ".txt", ".json", ".yaml", ".yml", ".eml")
_IMAGE_EXT = (".jpg", ".jpeg", ".png")
_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def front_matter(text: str) -> tuple[dict[str, str], str]:
    """`key: value` lines of a leading `---` block, and the body after it."""
    m = _FRONT.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep:
            meta[k.strip().lower()] = v.strip().strip('"')
    return meta, text[m.end():]


def ticket_status(text: str) -> str | None:
    meta, body = front_matter(text)
    if "status" in meta:
        return meta["status"].lower()
    m = re.search(r"^\s*\**status\**\s*[:=]\s*\**\s*(\w+)", body, re.I | re.M)
    return m.group(1).lower() if m else None


def resolve_request(instruction: str, paths: list[str], read: Callable[[str], str],
                    transcribe: Callable[[str], str] | None = None) -> Request:
    """The text that says what to do. `paths` are the task's workspace paths; `read(path)` returns a text file;
    `transcribe(path)` (optional, build ticket 08) the text of an image."""
    req = Request(text=instruction)
    if "/tickets/" in instruction:
        tickets = sorted(p for p in paths if p.startswith("/tickets/"))
        images = [p for p in tickets if p.lower().endswith(_IMAGE_EXT)]
        for p in [p for p in tickets if p.lower().endswith(_TEXT_EXT)] + (images if transcribe else []):
            text = transcribe(p) if p in images and transcribe else read(p)
            if ticket_status(text) == "open":
                req = Request(text=front_matter(text)[1].strip() or text, via="ticket", source=p)
                break
        else:
            req = Request(text=None, via="ticket", unread_images=[] if transcribe else images)
    if req.text and "/inbox/" in req.text:
        for p in sorted(p for p in paths if p.startswith("/inbox/") and p.lower().endswith(_TEXT_EXT)):
            req.messages[p] = read(p)
        if req.messages and req.via == "instruction":
            req.via, req.source = "inbox", next(iter(req.messages))
    return req


# --- trusted rules ----------------------------------------------------------------------------------------------------
_OPERATOR = re.compile(r"operator files?(?:[^.\n]|\.(?=\w))*", re.I)  # to the end of the sentence
_PATH = re.compile(r"(/[\w./-]+\.(?:md|txt))")


def operator_files(agents_md: str) -> list[str]:
    """`/AGENTS.md` plus the files it calls operator files (only those sentences count)."""
    out = ["/AGENTS.md"]
    for sent in _OPERATOR.findall(agents_md):
        out += [p for p in _PATH.findall(sent) if p not in out]
    return out


_KEEP = re.compile(r"rule|outcome|tier|trust|instruction|polic|never|must", re.I)


def condense_rules(text: str, limit: int = RULES_CHARS) -> str:
    """Rules verbatim when short; above `limit`, the sections whose heading names rules, outcomes, tiers or
    instructions (verbatim), cut to `limit`."""
    if len(text) <= limit:
        return text
    parts = re.split(r"(?m)^(?=#{1,3} )", text)
    kept = [p for p in parts if _KEEP.search(p.split("\n", 1)[0])] or parts
    return "\n".join(s.strip() for s in kept)[:limit]


def trusted_rules(paths: list[str], read: Callable[[str], str]) -> tuple[str, list[str]]:
    """(rules text for the prompts, operator file paths). Only `/AGENTS.md` goes into the rules text; the other
    operator files (the runbook) are definitions the solvers and the loop read on demand."""
    if "/AGENTS.md" not in paths:
        return "", []
    agents_md = read("/AGENTS.md")
    return condense_rules(agents_md), [p for p in operator_files(agents_md) if p in paths or p == "/AGENTS.md"]


# --- classification ---------------------------------------------------------------------------------------------------
def questions(with_message: bool) -> dict[str, dict]:
    if with_message:
        return {"action": llm.choice("What does the inbox message ask us to do?", INBOX_ACTIONS)}
    return {"family": llm.choice("Which task does the request ask for?", FAMILIES)}


def jev_state(req: Request) -> dict:
    state = {"request": (req.text or "")[:REQUEST_CHARS]}
    if req.messages:
        state["inbox"] = [front_matter(t)[1].strip()[:MESSAGE_CHARS] for t in req.messages.values()][:3]
    return state


def interpret(answers: dict, with_message: bool) -> Intake:
    """Jev's answers → family, confidence, kind and allowlist. A refusal or missing answer counts as 'other' (inbox:
    action 'none', no allowlist)."""
    q = answers.get("action" if with_message else "family") or {}
    choice = q.get("choice") if q.get("type", "choice") == "choice" else None
    probs = {k: float(v) for k, v in (q.get("probabilities") or {}).items()}
    confidence = probs.get(choice, float(q.get("confidence") or 0.0)) if choice else 0.0
    action = None
    if with_message:
        family = "inbox"
        action = choice if choice in INBOX_ACTIONS else "none"
        kind = f"action:{action}" if action != "none" else "answer"
    else:
        family = choice if choice in FAMILIES else "other"
        kind = KIND_BY_FAMILY.get(family, "answer")
    it = Intake(family=family, confidence=confidence, kind=kind, allowlist=[], action=action, probabilities=probs)
    if it.confident:
        it.allowlist = sorted(AUTHORISED.get(kind, ()))
    return it


def classify(req: Request, decide: Callable[[dict, dict], dict]) -> Intake:
    """One Jev call. `decide(state, questions)` is `Pacer.decide` (or a fake in tests). An unread photo ticket has no
    request text yet: no call, family 'other'."""
    if not req.text:
        return Intake(family="other", confidence=0.0, kind="answer", allowlist=[])
    with_message = bool(req.messages)
    return interpret(decide(jev_state(req), questions(with_message)), with_message)
