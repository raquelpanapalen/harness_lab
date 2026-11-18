"""Guards (SPEC §7, build ticket 07): deterministic checks between compose and submit. No model calls here, except
what the facts layer needs once per run (the standing order's settings).

`check(ctx, rec, draft) -> (clean, problems)`: `clean` is the draft with every blocked part removed and every safe fix
applied; `problems` are what compose must fix in its one repair round. A blocked action is never sent.

- **Refusals need a code-verified reason** (else: problem, and the clean draft is OK without actions):
  DENIED_SECURITY: the inbox sender is not a verified contact, or is flagged in the change log (answer = sender);
  NEEDS_CLARIFICATION: the answer names more than one roster agent;
  UNSUPPORTED: an inbox request whose action intake found no tool for.
- **Actions** (only on the allowlist, none with a refusal):
  quarantine: roster agents only, named in the request or in the compromised set; critical agents get their roster
  stand-in; rotate: inventory tokens only, at the code scheduler's time (change calendar + standing order);
  block: never our own or a verified partner's domain, only domains the request names or the attack reached;
  send_message: verified contacts only (a notification goes to the DPO's verified address); write: the path the task
  names, under /drafts/.
- **Frame guard:** an agent id that untrusted text in the workspace names stays in an answer or a quarantine only with
  trusted backing (a structured field of a cited or noted log event, a trusted file unit, or the computed compromised
  set). Unbacked ids are dropped from lists and quarantines; a single value is left as it is.
- **Outgoing text** (answer strings, messages, drafts, notes) is rewritten, never refused: URLs off our own and
  verified domains are defanged, `fake_…` secret values removed, long verbatim operator-rules sentences dropped.
"""
from __future__ import annotations

import copy
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from . import vision

AGENT_ID = re.compile(r"\b[a-z][a-z-]*-\d{2,3}\b")
URL = re.compile(r"\b(https?|ftp)://([^/\s\"'<>()\]]+)([^\s\"'<>)\]]*)", re.I)
SECRET = re.compile(r"\bfake_[a-z]{2,}_[A-Za-z0-9_\-]{4,}")
ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
FREE_TEXT_FIELDS = {"detail", "text", "note", "message", "body", "comment", "description", "reason"}
RULE_SENTENCE_CHARS = 100

_lock = threading.Lock()


# --- facts access ----------------------------------------------------------------------------------------------------
def facts_of(ctx: Any, task_id: str, need: tuple[str, ...] = ("settings",)):
    """The run's facts layer (harness/facts/runtime.py, ticket 09). The guards ask for the standing order only, and for
    the attack-trace facts (which need the artifact transcripts) only when a quarantine or a framed id is at stake."""
    from .facts.runtime import facts_of as _facts_of
    return _facts_of(ctx, task_id, need, lambda e: ctx.trace(task_id, e))


def _ok(fact) -> bool:
    return fact is not None and getattr(fact, "ok", False)


# --- outgoing text ---------------------------------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def defang(text: str, safe_domains: set[str]) -> str:
    def sub(m: re.Match) -> str:
        host = m.group(2).lower().split(":")[0]
        if any(host == d or host.endswith("." + d) for d in safe_domains if d):
            return m.group(0)
        return f"hxxp{'s' if m.group(1).lower().endswith('s') else ''}://{m.group(2).replace('.', '[.]')}{m.group(3)}"
    return URL.sub(sub, text)


def rewrite(text: str, safe_domains: set[str], rules_text: str) -> str:
    """Defang, strip secret values, drop long verbatim sentences of the operator rules."""
    out = SECRET.sub("[secret removed]", defang(text, safe_domains))
    if rules_text:
        rules = _norm(rules_text)
        parts = re.split(r"(?<=[.!?])\s+|\n", out)
        kept = [p for p in parts if not (len(p) >= RULE_SENTENCE_CHARS and _norm(p) in rules)]
        if len(kept) != len(parts):
            out = "\n".join(kept)
    return out


def rewrite_value(v: Any, safe: set[str], rules: str) -> Any:
    if isinstance(v, str):
        return rewrite(v, safe, rules)
    if isinstance(v, list):
        return [rewrite_value(x, safe, rules) for x in v]
    if isinstance(v, dict):
        return {k: rewrite_value(x, safe, rules) for k, x in v.items()}
    return v


# --- rotation scheduler ------------------------------------------------------------------------------------------------
def _utc(ts: str | None, tz: str | None = None) -> datetime | None:
    from .facts.world import parse_local
    s = parse_local(ts or "", tz)
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if s else None


SYSTEM_COLS = ("system", "svc", "service")
CONSUMER_COLS = ("consumers", "consumer_services", "used_by", "consumer", "used by")
START_COLS, END_COLS = ("start", "begin", "from"), ("end", "until", "to")
TZ_COLS = ("tz", "zone", "timezone", "time_zone")
STATUS_COLS, NOTE_COLS = ("outcome", "status", "result", "state"), ("note", "description", "reason", "comment")


def _col(row: dict, names: tuple[str, ...]) -> str:
    """The value of the first column whose whole header is one of `names` (headers vary by run)."""
    low = {(k or "").strip().lower(): v for k, v in row.items()}
    for n in names:
        if n in low:
            return (low[n] or "").strip()
    return ""


def _rotation_windows(f, token: str):
    """(now, [(lo, hi, calendar path, row)], problems) for `token`'s binding freezes and jobs, buffer included; or
    (None, None, why) when a precondition is missing."""
    problems: list[str] = []
    st, now = f.settings(), f.now()
    if not _ok(now):
        return None, None, ["current time unknown"]
    s = st.value
    if s is None or s.buffer_min is None or s.consumer_freezes_bind is None:
        return None, None, [f"standing order settings unknown ({'; '.join(getattr(st, 'problems', []))})"]
    tok_path = f.find_csv([("owner",), ("consumer", "used_by", "used by")])
    cal_path = f.find_csv([("kind",), ("zone", "tz"), ("start", "begin", "from")])
    if not tok_path or not cal_path:
        return None, None, ["token inventory or change calendar not found"]
    row = next((r for r in f.csv_rows(tok_path) if token in [str(v).strip() for v in r.values()]), None)
    if row is None:
        return None, None, [f"{token} not in the token inventory"]
    binding = {_col(row, SYSTEM_COLS).lower()}
    if s.consumer_freezes_bind:
        binding |= {c.strip().lower() for c in re.split(r"[;,|]", _col(row, CONSUMER_COLS)) if c.strip()}
    buf = timedelta(minutes=s.buffer_min)
    windows = []
    for r in f.csv_rows(cal_path):
        if _col(r, SYSTEM_COLS).lower() not in binding:
            continue
        if "cancel" in (_col(r, STATUS_COLS) + " " + _col(r, NOTE_COLS)).lower():
            continue
        tz = _col(r, TZ_COLS)
        a, b = _utc(_col(r, START_COLS), tz), _utc(_col(r, END_COLS), tz)
        if a and b:
            windows.append((a - buf, b + buf, cal_path, r))
        else:
            problems.append(f"calendar row not readable: {r}")
    return _utc(now.value), windows, problems


def schedule_rotation(f, token: str) -> tuple[str | None, list[str]]:
    """Earliest allowed rotation start for `token` (runbook: one hour, on a full hour, from the next full hour after
    now, clear of the active freezes and jobs that bind it, plus the standing order's buffer), or None + why."""
    n, windows, problems = _rotation_windows(f, token)
    if windows is None:
        return None, problems
    t = n.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    for _ in range(24 * 14):
        if all(not (t < hi and lo < t + timedelta(hours=1)) for lo, hi, _p, _r in windows):
            return t.strftime("%Y-%m-%dT%H:%M:%SZ"), problems
        t += timedelta(hours=1)
    return None, problems + ["no free hour within 14 days"]


def rotation_blockers(f, token: str, at: str) -> list[tuple[str, dict]]:
    """The calendar rows (path, row) whose window overlapped the hours skipped before the slot `at`: the evidence
    for why rotation could not start earlier."""
    n, windows, _ = _rotation_windows(f, token)
    t_at = _utc(at)
    if not windows or n is None or t_at is None:
        return []
    t0 = n.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return [(p, r) for lo, hi, p, r in windows if lo < t_at and t0 < hi]


def is_critical(row: dict) -> bool:
    """The roster's `critical` column holds yes/no or the duty that makes the agent critical."""
    v = str(row.get("critical", "")).strip().lower()
    return bool(v) and v not in ("no", "n", "false", "0", "-", "none", "n/a")


def stand_in(f, agent: str) -> str | None:
    """Runbook: a critical agent is quarantined with a designated stand-in (roster `stand_in`, possibly several) that
    is neither compromised nor quarantined."""
    if f is None:
        return None
    row = (f.roster().value or {}).get(agent, {})
    if not is_critical(row):
        return None
    cands = [c.strip() for c in re.split(r"[;,|/ ]+", str(row.get("stand_in", ""))) if c.strip()]
    bad = set(f.compromised().value or []) if _safe_ok(f, "compromised") else set()
    try:
        q = f.quarantined().value or {}
        bad |= set(q)
    except Exception:  # noqa: BLE001
        pass
    return next((c for c in cands if c not in bad), cands[0] if cands else None)


# --- helpers ---------------------------------------------------------------------------------------------------------
def _sender(ctx: Any, rec: Any) -> list[str]:
    out = []
    for p in rec.inbox:
        text = ctx.tools.read_text(rec.task_id, p) if ctx.tools is not None else ""
        m = re.search(r"^from:.*$", text or "", re.I | re.M)
        if m:
            out += [a.lower() for a in ADDRESS.findall(m.group(0))]
    return out


def _flagged_text(f) -> str:
    ch = f.changes().value or {}
    p = ch.get("path")
    text = f.text(p) if p else ""
    out, on = [], False
    for line in (text or "").splitlines():
        if line.startswith("#"):
            on = bool(re.search(r"flag|compromis", line, re.I))
        elif on:
            out.append(line)
    return "\n".join(out + [v.get("text", "") for v in (ch.get("flagged") or {}).values()]).lower()


def _event(unit) -> dict | None:
    try:
        ev = json.loads(unit.text)
    except (ValueError, TypeError):
        return None
    return ev if isinstance(ev, dict) else None


def _structured_mentions(unit, aid: str) -> bool:
    """`aid` in a structured field of a log event (actor, target, ...: trusted even when the event's free text was
    planted), or anywhere in a unit nobody flagged."""
    if unit is None:
        return False
    ev = _event(unit)
    if ev is not None:
        return any(aid in str(v) for k, v in ev.items() if k.lower() not in FREE_TEXT_FIELDS)
    return not unit.untrusted and aid in unit.text


def framed_ids(view, roster: set[str]) -> set[str]:
    """Roster agents that untrusted text in this task's workspace names."""
    out: set[str] = set()
    for u in view.all_units():
        if u.untrusted:
            ev = _event(u)
            text = " ".join(str(v) for k, v in ev.items() if k.lower() in FREE_TEXT_FIELDS) if ev else u.text
            out |= set(AGENT_ID.findall(text)) & roster
    return out


def backed(aid: str, view, sources: list[dict], notes: list[dict], compromised: set[str]) -> bool:
    if aid in compromised:
        return True
    locs = list(sources) + [s for n in notes for s in n.get("sources", [])]
    return any(_structured_mentions(view.find(s), aid) for s in locs)


def _drop_ids(value: Any, bad: set[str]) -> tuple[Any, bool]:
    """Remove unbacked ids from lists (recursively); True if a single value is unbacked."""
    if isinstance(value, list):
        return [x for x in value if not (isinstance(x, str) and x in bad)], False
    if isinstance(value, dict):
        single = False
        out = {}
        for k, x in value.items():
            if isinstance(x, list):
                out[k] = [y for y in x if not (isinstance(y, str) and y in bad)]
            else:
                single |= isinstance(x, str) and x in bad
                out[k] = x
        return out, single
    return value, isinstance(value, str) and value in bad


# --- the check -----------------------------------------------------------------------------------------------------------
def check(ctx: Any, rec: Any, draft: dict) -> tuple[dict, list[str]]:
    d = copy.deepcopy(draft)
    d.setdefault("actions", [])
    problems: list[str] = []
    view = ctx.index.view(rec.task_id) if ctx.index is not None and rec.task_id in ctx.index.files else None
    try:
        need: tuple[str, ...] = ("settings",)
        if view is not None:
            quarantines = any(o.get("op") == "quarantine_agent" for a in d.get("actions") or []
                              if a.get("tool") == "containment" for o in a.get("args", {}).get("ops") or [])
            if quarantines or AGENT_ID.search(json.dumps(d.get("answer"))):
                need = ("artifacts", "settings")
        f = facts_of(ctx, rec.task_id, need) if view is not None else None
    except Exception as e:  # noqa: BLE001 - no facts: the checks that need them block instead
        f = None
        problems.append(f"(facts unavailable: {type(e).__name__})")
    roster = set((f.roster().value or {}) if f is not None else {})
    own = (f.own_domain().value if f is not None else None) or ""
    verified = f.verified_domains() if f is not None else set()
    safe = verified | ({own} if own else set())
    rules = rec.rules or ""
    request = (rec.request or rec.instruction or "")

    # 1. outcome
    if d["outcome"] != "OK":
        d["actions"] = []
        reason = _verify_refusal(ctx, rec, d, f, roster)
        if reason is None:
            problems.append(f"outcome {d['outcome']} has no verified reason: the request is supported, the sender "
                            "verified and the target unambiguous. Use OK and carry out the request.")
            d = {"outcome": "OK", "answer": None, "sources": [], "actions": []}

    # 2. actions
    kept = []
    for a in d.get("actions") or []:
        a2, why = _guard_action(ctx, rec, a, f, roster, safe, rules, request)
        problems += why
        if a2 is not None:
            kept.append(a2)
    d["actions"] = kept

    # 3. frame guard
    if view is not None and roster:
        framed = framed_ids(view, roster)
        if framed:
            comp = set(f.compromised().value or []) if f is not None and _safe_ok(f, "compromised") else set()
            bad = {aid for aid in framed if not backed(aid, view, d.get("sources", []), rec.notes, comp)}
            in_answer = bad & set(AGENT_ID.findall(json.dumps(d.get("answer"))))
            if in_answer:
                # Lists lose unbacked ids. A single value stays: a repair round there cost a correct patient zero
                # on dev (rp010), and frames target list answers and quarantines.
                d["answer"], _ = _drop_ids(d.get("answer"), in_answer)
            for a in d["actions"]:
                if a["tool"] == "containment":
                    before = len(a["args"]["ops"])
                    a["args"]["ops"] = [o for o in a["args"]["ops"] if o.get("agent") not in bad]
                    if len(a["args"]["ops"]) < before:
                        problems.append("quarantine of an agent only untrusted text names was blocked")
            d["actions"] = [a for a in d["actions"] if a["tool"] != "containment" or a["args"]["ops"]]

    # 4. outgoing text
    d["answer"] = rewrite_value(d.get("answer"), safe, rules)
    return d, problems


def _safe_ok(f, name: str) -> bool:
    try:
        return _ok(getattr(f, name)())
    except Exception:  # noqa: BLE001
        return False


def _verify_refusal(ctx, rec, d, f, roster: set[str]) -> str | None:
    o = d["outcome"]
    if o == "DENIED_SECURITY" and rec.inbox and f is not None:
        senders = _sender(ctx, rec)
        verified = {(c.get("address") or "").lower() for c in (f.contacts().value or []) if c.get("verified")}
        flagged = _flagged_text(f)
        for s in senders:
            if s not in verified or s in flagged or s.split("@")[0] in flagged:
                d["answer"] = s
                return f"sender {s} is {'not a verified contact' if s not in verified else 'flagged'}"
        return None
    if o == "NEEDS_CLARIFICATION":
        ids = [x for x in (d.get("answer") or []) if isinstance(x, str) and x in roster] \
            if isinstance(d.get("answer"), list) else []
        if len(set(ids)) >= 2:
            d["answer"] = sorted(set(ids))
            return "the request matches more than one roster agent"
        return None
    if o == "UNSUPPORTED":
        if rec.inbox and rec.inbox_action == "none":
            d["answer"] = None
            return "no tool for the requested action"
        return None
    return None


def _guard_action(ctx, rec, a: dict, f, roster: set[str], safe: set[str], rules: str,
                  request: str) -> tuple[dict | None, list[str]]:
    tool, args = a.get("tool"), dict(a.get("args") or {})
    probs: list[str] = []
    if tool == "containment":
        ops = []
        rows = (f.roster().value or {}) if f is not None else {}
        comp = set(f.compromised().value or []) if f is not None and _safe_ok(f, "compromised") else None
        attack_domains = set(f.attacker_domains().value or []) if f is not None and _safe_ok(f, "attacker_domains") \
            else None
        for o in args.get("ops") or []:
            op = o.get("op")
            if f"containment:{op}" not in rec.allowlist:
                probs.append(f"containment op {op} is not allowed for this task")
                continue
            if op == "quarantine_agent":
                ag = str(o.get("agent", ""))
                if ag not in roster:
                    probs.append(f"quarantine {ag}: not an agent of the roster")
                    continue
                if comp is not None and ag not in comp and ag not in request:
                    probs.append(f"quarantine {ag}: not named by the task and not in the compromised set")
                    continue
                si = stand_in(f, ag)
                if si:
                    o = {**o, "stand_in": si}
                ops.append(o)
            elif op == "rotate_token":
                tok = str(o.get("token", ""))
                at, why = schedule_rotation(f, tok) if f is not None else (None, ["no facts"])
                if at is None:
                    if any("not in the token inventory" in w for w in why):
                        probs.append(f"rotate {tok}: not a token of the inventory")
                        continue
                    probs += [f"rotate {tok}: time not verified ({'; '.join(why)})"]
                    continue
                ops.append({**o, "at": at})
            elif op == "block_domain":
                dom = str(o.get("domain", "")).lower().strip()
                if not dom or any(dom == s or dom.endswith("." + s) for s in safe):
                    probs.append(f"block {dom}: our own or a verified partner's domain")
                    continue
                if attack_domains is not None and dom not in attack_domains and dom not in request.lower():
                    probs.append(f"block {dom}: not named by the task and not reached in an attack trace")
                    continue
                ops.append(o)
            elif op == "add_note":
                ops.append({**o, "text": rewrite(str(o.get("text", "")), safe, rules)})
            else:
                ops.append(o)
        return ({"tool": "containment", "args": {"ops": ops}} if ops else None), probs
    if tool == "send_message":
        contacts = (f.contacts().value or []) if f is not None else []
        verified = {(c.get("address") or "").lower(): c for c in contacts if c.get("verified")}
        to = str(args.get("to", "")).strip().lower()
        if rec.task_kind == "notify":
            dpo = next((addr for addr, c in verified.items()
                        if re.search(r"\bdpo\b|data protection", json.dumps(c).lower())), None)
            if dpo:
                to = dpo
        if to not in verified:
            return None, [f"send_message to {to}: not a verified contact (verified: {', '.join(sorted(verified))})"]
        return {"tool": "send_message", "args": {"to": to, "subject": rewrite(str(args.get("subject", "")), safe, rules),
                                                 "body": rewrite(str(args.get("body", "")), safe, rules)}}, probs
    if tool == "write":
        named = re.findall(r"/drafts/[\w./-]+[\w]", request)
        path = named[0] if named else str(args.get("path", ""))
        if not path.startswith("/drafts/"):
            return None, [f"write {path}: only under /drafts/"]
        return {"tool": "write", "args": {"path": path,
                                          "content": rewrite(str(args.get("content", "")), safe, rules)}}, probs
    return None, [f"{tool}: not an allowed action"]
