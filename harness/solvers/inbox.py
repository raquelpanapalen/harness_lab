"""Inbox routing (build ticket 12): an inbox request is decided by code under the workspace rules.

1. **Sender** (from the message header): not a verified contact, or flagged in the change log → DENIED_SECURITY,
   answer = the sender's address. Nothing else is done.
2. **What it asks** (intake's Jev action, cross-checked by the message's verbs):
   - remove / take away a scope or permission → UNSUPPORTED (no tool), answer null;
   - quarantine an agent → the reference (id, name, role, owner, nickname) resolved against the roster: more than one
     match → NEEDS_CLARIFICATION with every candidate id; one → quarantine it (with its stand-in);
   - block domains → the domains the message names;
   - send / ask / share by e-mail → to the address the message gives, or the verified contact whose role it names,
     with the facts it asks for computed in code (attacker domains, record count, incident id) and its own request
     restated.
   Anything else → not confident (the loop takes over).
The guards still check every action (verified recipient, roster, own/partner domains) before it is sent.
"""
from __future__ import annotations

import re
from typing import Any

from .. import guards
from ..vision import body as _strip_marks  # noqa: F401  (kept for symmetry with transcribed messages)
from . import request_of
from .response import AGENT_ID, DOMAIN, Result, _exfil, resolve_agent

ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def parse_message(text: str) -> tuple[dict[str, str], str]:
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text or "", re.S)
    head: dict[str, str] = {}
    if m:
        for line in m.group(1).splitlines():
            k, sep, v = line.partition(":")
            if sep:
                head[k.strip().lower()] = v.strip()
    return head, (text[m.end():] if m else text or "").strip()


def _flagged(f, sender: str) -> bool:
    text = guards._flagged_text(f)
    return sender in text or sender.split("@")[0] in text


def _rows(f, path: str | None, needle: str | None, n: int = 1) -> list:
    """The first `n` evidence units of `path` (rows / lines, not the file) whose text names `needle`."""
    if not path or not needle or not hasattr(f, "v"):
        return []
    return [u for u in f.v.units(path)[1:] if needle.lower() in u.text.lower()][:n]


def _path(fact) -> str | None:
    return next((u.path for u in getattr(fact, "evidence", None) or [] if u is not None), None)


def _cite(f, r: Result, msg_path: str | None, sender: str) -> Result:
    """Citations per outcome (measured on the dev runs, ticket 12 follow-up): the inbox message always; plus the
    change-log lines naming a refused sender, the roster row of each agent named (clarification candidates or the
    quarantined agent), or the contact row of a message's recipient."""
    units = f.v.units(msg_path)[:1] if msg_path and hasattr(f, "v") else []
    if r.outcome == "DENIED_SECURITY":
        ch = (f.changes().value or {}).get("path")
        units += _rows(f, ch, sender, 2) or _rows(f, ch, sender.split("@")[0], 2)
    elif r.outcome == "NEEDS_CLARIFICATION":
        rp = _path(f.roster())
        units += [u for a in r.answer or [] for u in _rows(f, rp, a)]
    elif r.actions and r.actions[0]["tool"] == "containment":
        rp = _path(f.roster())
        units += [u for o in r.actions[0]["args"]["ops"] if o["op"] == "quarantine_agent"
                  for u in _rows(f, rp, o["agent"])]
    elif r.actions and r.actions[0]["tool"] == "send_message":
        units += _rows(f, _path(f.contacts()), r.actions[0]["args"]["to"])
    r.units = units
    return r


def route(f, request: str, message: str, action: str | None, allow: list[str], msg_path: str | None = None) -> Result:
    r = _route(f, request, message, action, allow)
    if r.confident:
        head, _ = parse_message(message)
        sender = (ADDRESS.findall(head.get("from", "")) or [""])[0].lower()
        _cite(f, r, msg_path, sender)
    return r


def _route(f, request: str, message: str, action: str | None, allow: list[str]) -> Result:
    r = Result()
    head, body = parse_message(message)
    senders = [a.lower() for a in ADDRESS.findall(head.get("from", ""))]
    if not senders:
        return r.fail("no sender in the message header")
    sender = senders[0]
    # fail closed: without the contacts and the change log the sender cannot be checked (AGENTS.md hard rules)
    for fact, what in ((f.contacts(), "contacts"), (f.changes(), "change log")):
        if not fact.ok:
            return r.fail(f"{what} not found: the sender cannot be checked")
    contacts = f.contacts().value or []
    verified = {(c.get("address") or "").lower(): c for c in contacts if c.get("verified")}
    if sender not in verified or _flagged(f, sender):
        r.outcome, r.answer, r.confident = "DENIED_SECURITY", sender, True
        return r
    low = body.lower()
    if re.search(r"\b(?:take|remove|revoke|strip|withdraw)\b[^.]*\b(?:scope|permission|access|right)s?\b|"
                 r"\b(?:scope|permission)s?\b[^.]*\baway\b|\btake\b[^.]*\baway\b|"
                 r"\b(?:remove|revoke|withdraw)\s+`?[a-z_]+:[a-z_]+", low):
        r.outcome, r.answer, r.confident = "UNSUPPORTED", None, True
        return r
    verb = action or "none"
    if re.search(r"\bquarantin|\bisolat", low):
        verb = "quarantine_agent"
    elif re.search(r"\bblock\b", low):
        verb = "block_domain"
    elif re.search(r"\b(?:send|e-?mail|ask|share|heads-up|notify|forward|tell)\b", low) and verb == "none":
        verb = "send_message"
    if verb == "quarantine_agent":
        clean_body = re.sub(r"\([^)]*\)", " ", body)  # "(stand-in if required)" is not a target
        refs = AGENT_ID.findall(clean_body)
        if not refs:
            m = re.search(r"([\w'’ -]+?)\s+(?:needs|has|must|should)\s+(?:to\s+)?(?:go|be put|be placed|be moved)\s+"
                          r"in(?:to)?\s+quarantine", clean_body, re.I) or \
                re.search(r"quarantin\w*\s+(\w.+?)(?:\s+(?:now|immediately|today|right away|asap)\b|[.,;]|$)",
                          clean_body, re.I)
            refs = [m.group(1).strip()] if m and m.group(1).strip() else []
        if len(refs) != 1:
            return r.fail(f"quarantine target not found ({refs})")
        hits = resolve_agent(f, refs[0])
        if len(hits) > 1:
            r.outcome, r.answer, r.confident = "NEEDS_CLARIFICATION", hits, True
            return r
        if not hits:
            return r.fail(f"no roster agent matches {refs[0]!r}")
        si = guards.stand_in(f, hits[0])
        op = {"op": "quarantine_agent", "agent": hits[0], **({"stand_in": si} if si else {})}
        r.actions = [{"tool": "containment", "args": {"ops": [op]}}] if "containment:quarantine_agent" in allow else []
        r.answer, r.confident = f"Quarantined {hits[0]}" + (f" (stand-in {si})" if si else "") + ".", bool(r.actions)
        return r
    if verb == "block_domain":
        doms = sorted({d.lower() for d in DOMAIN.findall(body) if "@" not in d})
        own = f.own_domain().value or ""
        safe = f.verified_domains() | ({own} if own else set())
        doms = [d for d in doms if not any(d == s or d.endswith("." + s) for s in safe)]
        used = []
        if re.search(r"attacker domain", low):  # "block every attacker domain from that list": runbook definition
            ad = f.attacker_domains()
            used.append(ad)
            doms = [d for d in doms if d in set(ad.value or [])]
        if not doms:
            return r.fail("no domain to block")
        ops = [{"op": "block_domain", "domain": d} for d in doms]
        r.actions = [{"tool": "containment", "args": {"ops": ops}}] if "containment:block_domain" in allow else []
        r.answer = f"Blocked {', '.join(doms)}."
        r.confident = bool(r.actions) and all(getattr(u, "ok", False) for u in used)
        return r
    if verb == "send_message":
        to = None
        for a in ADDRESS.findall(body):
            if a.lower() in verified and a.lower() != sender:
                to = a.lower()
                break
        if to is None:  # a role or name: "the on-call lead", "Lindqvist & Partners"
            best = None
            for addr, c in verified.items():
                if addr == sender:
                    continue
                for k, v in c.items():
                    v = str(v).strip().lower()
                    if k in ("address", "verified", "internal", "id") or len(v) < 4:
                        continue
                    core = re.split(r"\s*\(", v)[0]
                    if core and core in low and (best is None or len(core) > best[0]):
                        best = (len(core), addr)
            to = best[1] if best else None
        if to is None:
            return r.fail("recipient not found among verified contacts")
        lines = []
        inc = re.search(r"\bINC-[A-Z0-9-]+\b", message) or re.search(r"\bINC-[A-Z0-9-]+\b", f.text("/AGENTS.md") or "")
        if inc:
            lines.append(f"Incident: {inc.group(0)}")
        used = []
        if re.search(r"attacker domain|indicator", low):
            ad = f.attacker_domains()
            used.append(ad)
            lines.append("Attacker domains: " + ", ".join(ad.value or []))
        if re.search(r"number of (?:exfiltrated|affected)|records", low):
            st, ex = _exfil(f)
            used += [st, ex]
            if ex.value is not None:
                lines.append(f"Exfiltrated customer records: {ex.value['records']}")
        ask = " ".join(s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if re.search(r"\bplease\b|\bask\b", s, re.I))
        if ask and not used:
            ask = re.sub(r"(?i)^please (?:ask [^.]*?by e-?mail to|send [^.]*?)\s*", "Please ", ask)
            lines.append(f"Request: {ask}")
        subject = head.get("subject") or "Incident update"
        r.actions = [{"tool": "send_message", "args": {"to": to, "subject": subject, "body": "\n".join(lines)}}] \
            if "send_message" in allow else []
        r.answer = f"Sent the requested information to {to}."
        r.confident = bool(r.actions) and all(getattr(u, "ok", False) for u in used)
        r.problems += [p for u in used for p in getattr(u, "problems", [])]
        return r
    return r.fail(f"request not recognised (action {verb})")


ALL = ["containment:quarantine_agent", "containment:block_domain", "send_message"]


def solve_inbox(ctx: Any, rec: Any, f) -> Result:
    """Route the task's inbox message. When code is confident, the task's kind and allowlist follow its decision
    (code-verified routing outranks intake's guess); a refusal empties the allowlist."""
    text = ctx.tools.read_text(rec.task_id, rec.inbox[0]) if rec.inbox and ctx.tools is not None else None
    if not text:
        return Result().fail("no inbox message")
    r = route(f, request_of(rec), text, rec.inbox_action, ALL, rec.inbox[0])
    if r.confident and r.outcome != "OK":
        rec.allowlist = []
        if r.outcome == "UNSUPPORTED":
            rec.inbox_action = "none"  # the guards accept UNSUPPORTED only for a request no tool can do
    elif r.confident:
        tools = {f"containment:{o['op']}" if a["tool"] == "containment" else a["tool"]
                 for a in r.actions for o in (a["args"].get("ops") or [{}])}
        rec.allowlist = sorted(tools)
        if r.actions:
            a = r.actions[0]
            rec.task_kind = "action:" + (a["args"]["ops"][0]["op"] if a["tool"] == "containment" else a["tool"])
    return r
