"""Wave 1 (build ticket 10): compromised agents, patient zero, timeline. Thin queries on the fact layer.

Each solver checks its own preconditions (answer shape, keys the request asks for, facts ok) and is confident only
when all hold; otherwise it returns its partial facts as loop notes.
"""
from __future__ import annotations

import json
import re
from typing import Any

from arena import llm

from ..facts.world import Fact, Facts, Settings, parse_local
from ..state import TaskRecord
from . import Precondition, Solution, note, register


def _fact_solution(fact: Fact, answer: Any, sources: list[dict], notes: list[dict], why: str = "") -> Solution:
    ok = fact.ok and not why
    return Solution(answer=answer, sources=sources, confident=ok, notes=notes,
                    why=why or "; ".join(fact.problems[:3]))


# --- compromised agents ---------------------------------------------------------------------------------------------
@register("compromised")
def compromised(ctx: Any, rec: TaskRecord, facts) -> Solution:
    if rec.answer_shape not in ("list", "unknown"):
        raise Precondition(f"answer shape {rec.answer_shape}, expected a list")
    f: Facts = facts(("artifacts",))
    c = f.compromised()
    srcs = [u.source() for u in c.evidence]
    notes = [note(f"{u.text[:160]} (action within an attack trace)", [u]) for u in c.evidence]
    return _fact_solution(c, sorted(c.value), srcs, notes)


# --- patient zero -----------------------------------------------------------------------------------------------------
def requested_keys(request: str) -> list[str]:
    """The keys of the JSON object the request spells out ({"artifact": ..., "agent": ..., "at": ...})."""
    m = re.search(r"\{[^{}]*\}", request or "")
    return re.findall(r'"([A-Za-z_][\w ]*)"\s*:', m.group(0)) if m else []


@register("patient_zero")
def patient_zero(ctx: Any, rec: TaskRecord, facts) -> Solution:
    keys = requested_keys(rec.request or rec.instruction)
    roles = {}
    for k in keys:
        low = k.lower()
        role = "artifact" if "artifact" in low or "entry" in low or "path" in low else \
            "agent" if "agent" in low or "who" in low else \
            "at" if low in ("at", "time", "when", "ts", "timestamp") or "time" in low else None
        if role is None or role in roles.values():
            raise Precondition(f"unexpected answer key {k!r}")
        roles[k] = role
    if set(roles.values()) != {"artifact", "agent", "at"}:
        raise Precondition(f"answer keys {keys} are not artifact/agent/time")
    f: Facts = facts(("artifacts",))
    pz = f.patient_zero()
    if not pz.value:
        return Solution(confident=False, why="; ".join(pz.problems[:3]))
    v = pz.value
    answer = {k: v[r] for k, r in roles.items()}
    notes = [note(f"patient zero {v['agent']} first processed {v['original']} at {v['at']}", pz.evidence)]
    return _fact_solution(pz, answer, [u.source() for u in pz.evidence], notes)


# --- timeline ---------------------------------------------------------------------------------------------------------
# Our catalogue of runbook events (descriptions, not dev wording); Jev maps each lettered event onto one.
EVENTS = {
    "entry_upload": "the entry artifact (the one that started the attack) was uploaded",
    "patient_zero": "patient zero / the first agent processed the entry artifact",
    "redteam_upload": "the authorised red-team (canary) artifact was uploaded",
    "first_exfil": "the first exfiltrating transfer of customer records succeeded",
    "approved_transfer": "the approved / authorised transfer to a partner or auditor was sent",
    "board_notice": "the injected notice or post was posted on the agents board",
    "siem_alert": "the SIEM raised an alert",
    "first_secret": "the first secret or token was read within the attack",
    "quarantine": "an agent was quarantined",
    "revocation": "an emergency revocation or permission change took effect",
    "other": "none of these",
}
LETTERED = re.compile(r"(?:^|[\s;:,(])([A-L])[:)]\s+(.+?)(?=(?:[;,]\s*|\s+)[A-L][:)]\s|\.\s+[A-Z]|\.?\s*$)", re.S)


def lettered_events(request: str) -> dict[str, str]:
    text = " ".join((request or "").split())
    out = {}
    for m in LETTERED.finditer(text):
        out.setdefault(m.group(1), m.group(2).strip().rstrip("."))
    letters = sorted(out)
    if len(letters) < 2 or letters != [chr(ord("A") + i) for i in range(len(letters))]:
        raise Precondition(f"lettered events not found ({letters})")
    return out


def classify_events(ctx: Any, rec: TaskRecord, events: dict[str, str]) -> dict[str, tuple[str, float]]:
    qs = {k: llm.choice(f"Which kind of incident event is this: {v!r}?", EVENTS) for k, v in events.items()}
    ans = ctx.pacer.decide({"events": events}, qs, log=lambda e: ctx.trace(rec.task_id, e))
    out = {}
    for k in events:
        a = ans.get(k) or {}
        ch = a.get("choice")
        p = float((a.get("probabilities") or {}).get(ch, a.get("confidence") or 0.0)) if ch else 0.0
        out[k] = (ch if ch in EVENTS else "other", p)
    return out


def event_time(f: Facts, kind: str, text: str) -> tuple[str | None, list, str]:
    """(UTC time, evidence units, problem) of one catalogued event, computed from the workspace."""
    ids = f.agent_ids()
    named = [a for a in ids if re.search(rf"(?<![\w-]){re.escape(a)}(?![\w-])", text)]
    if kind == "patient_zero":
        pz = f.patient_zero()
        return (pz.value or {}).get("at"), pz.evidence[:1], "" if pz.ok else "; ".join(pz.problems[:2])
    if kind in ("entry_upload", "redteam_upload"):
        arts = f.artifacts().value
        if kind == "entry_upload":
            pz = f.patient_zero()
            target = (pz.value or {}).get("original")
            prob = "" if pz.ok else "; ".join(pz.problems[:2])
        else:
            reds = [o for o, a in arts.items() if a["redteam"]]
            target = reds[0] if len(reds) == 1 else None
            prob = "" if len(reds) == 1 else f"{len(reds)} red-team artifacts"
        if not target:
            return None, [], prob or "artifact unknown"
        up = [e for e in f.events() if e["action"] == "upload_artifact" and e["target"] == target]
        if up:
            e = min(up, key=lambda e: (e["ts"] or "", e["line"]))
            return e["ts"], [f.ev_unit(e)], prob
        return None, [], f"no upload event for {target}"
    if kind == "first_secret":
        reads = [e for e in f.attack_events() if e["action"] == "read_secret" and str(e.get("status")) != "declined"]
        at = f.attack_traces()
        if not reads:
            return None, [], "no secret read in an attack trace"
        e = min(reads, key=lambda e: (e["ts"] or "", e["line"]))
        return e["ts"], [f.ev_unit(e)], "" if at.ok else "; ".join(at.problems[:2])
    if kind == "board_notice":
        posts = [e for e in f.attack_events() if e["action"] == "post_board"]
        at = f.attack_traces()
        if not posts:
            return None, [], "no board post in an attack trace"
        e = min(posts, key=lambda e: (e["ts"] or "", e["line"]))
        post = board_unit(f, e.get("target", ""))
        return e["ts"], [post or f.ev_unit(e)], "" if at.ok else "; ".join(at.problems[:2])
    if kind == "quarantine":
        if len(named) != 1:
            return None, [], f"quarantined agent not named once ({named})"
        q = [e for e in f.events() if e["action"] == "quarantine_agent" and e.get("target") == named[0]
             and str(e.get("status", "ok")).lower() == "ok"]
        if q:
            e = min(q, key=lambda e: (e["ts"] or "", e["line"]))
            return e["ts"], [f.ev_unit(e)], ""
        return None, [], f"no quarantine event for {named[0]}"
    if kind == "revocation":
        ops = re.findall(r"\b(remove_delegation|revoke_scope|add_delegation|grant_scope)\b", text)
        rows = [r for r in f.changes().value["rows"] if (not ops or r["change"] in ops)
                and all(a in (r["agent"], r["value"]) or a in r["value"] for a in named)]
        if len(rows) != 1:
            return None, [], f"{len(rows)} change-log rows match"
        r = rows[0]
        return r["time"], [f.v.find({"path": f.changes().value["path"], "line": r["line"]})], ""
    if kind in ("first_exfil", "approved_transfer"):
        tr = f.transfers()
        if not tr.ok:
            return None, [], "; ".join(tr.problems[:2])
        if kind == "first_exfil":
            x = f.exfil(Settings(count_blocked=False, dedupe=True))  # "succeeded": blocked never counts here
            t = x.value["first_succeeded"]
            u = [r["unit"] for r in tr.value if r["ts"] == t and r["id"] in x.value["transfers"]]
            return t, u[:1], ""
        cov = [r for r in tr.value if r["ticket"] and f.covered(r) and r["status"] == "ok"]
        if len(cov) != 1:
            return (cov[0]["ts"] if cov else None), [r["unit"] for r in cov[:1]], f"{len(cov)} approved transfers"
        return cov[0]["ts"], [cov[0]["unit"]], ""
    if kind == "siem_alert":
        return siem_time(f, text)
    return None, [], f"event kind {kind}"


def board_unit(f: Facts, msg_id: str):
    """The board's own line for a message id (the post itself is the evidence, not the gateway event)."""
    if not msg_id:
        return None
    for p in f.v.paths():
        if p.startswith("/board/") and p.endswith(".jsonl"):
            for u in f.v.units(p)[1:]:
                try:
                    if json.loads(u.text).get("id") == msg_id:
                        return u
                except ValueError:
                    continue
    return None


def siem_time(f: Facts, text: str) -> tuple[str | None, list, str]:
    """The SIEM alert the event describes: rows of the alert sheet whose text shares the event's key words (and,
    for 'secret read', mentions a secret/token/credential), raised for an agent of the attack."""
    named = [q for q in f.v.paths() if q.rsplit("/", 1)[-1].lower().startswith(("siem", "alert"))
             and q.lower().endswith((".csv", ".xlsx", ".ods"))]
    p = named[0] if len(named) == 1 else f.find_table([("alert",), ("time", "ts")])
    if not p:
        return None, [], "SIEM alerts not found"
    rows = f.table_rows(p)
    comp = set(f.compromised().value)
    keyw = [w for w in re.findall(r"[a-z]{5,}", text.lower()) if w not in ("raised", "alert", "about", "which")]
    cands = []
    for r in rows:
        line = " ".join(str(v) for k, v in r.items() if k != "_row").lower()
        if not any(a in line for a in comp):
            continue
        if "secret" in text.lower() and not re.search(r"secret|token|credential|vault", line):
            continue
        score = sum(w in line for w in keyw)
        ts = next((v for k, v in r.items() if k != "_row" and parse_local(str(v or ""))), None)
        cands.append((score, str(ts or ""), r))
    if not cands:
        return None, [], "no SIEM alert matches"
    best = max(c[0] for c in cands)
    top = sorted([c for c in cands if c[0] == best], key=lambda c: c[1])
    t = parse_local(top[0][1])
    sheet = f.v.sheets(p)[0] if f.v.sheets(p) else None
    u = f.v.find({"path": p, "sheet": sheet, "row": top[0][2]["_row"]}) if sheet else None
    return t, [u] if u else [], "" if len(top) == 1 else f"{len(top)} SIEM alerts match equally"


@register("timeline")
def timeline(ctx: Any, rec: TaskRecord, facts) -> Solution:
    events = lettered_events(rec.request or rec.instruction)
    kinds = classify_events(ctx, rec, events)
    f: Facts = facts(("artifacts",))
    times, srcs, notes, problems = {}, [], [], []
    for letter, text in events.items():
        kind, p = kinds[letter]
        if kind == "other" or p < 0.5:
            problems.append(f"{letter}: event kind unclear ({kind}, {p:.2f})")
            continue
        t, units, prob = event_time(f, kind, text)
        units = [u for u in units if u is not None]
        if prob:
            problems.append(f"{letter}: {prob}")
        if t is None:
            continue
        times[letter] = t
        srcs += [u.source() for u in units]
        notes.append(note(f"event {letter} ({text}) happened at {t}", units))
    if len(times) != len(events):
        return Solution(confident=False, notes=notes, why="; ".join(problems[:4]) or "missing event times")
    order = sorted(times, key=lambda k: times[k])
    if len(set(times.values())) != len(times):
        problems.append("two events at the same second")
    seen, uniq = set(), []
    for s in srcs:
        k = json.dumps(s, sort_keys=True)
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return Solution(answer=order, sources=uniq, confident=not problems, notes=notes, why="; ".join(problems[:4]))


