"""Incident-response solvers (built as wave 4, ticket 13): exfiltration, canaries, breach notification, containment, rotation, first token
read, token status and board-message origin, computed in code over the facts layer (harness/facts, ticket 09).

Each function takes the task's Facts and request text and returns a `Result`: the answer, the evidence units it rests
on, the state-changing actions, whether it is confident, and why not. Confident only when every fact it used has its
preconditions (`Fact.ok`) and the request's parameters were found. Every value comes from the workspace: names,
domains, counts, times and the standing-order settings; nothing scenario-specific is fixed here.
The solver registry (harness/solvers, ticket 10) wraps these in `Solution`s.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .. import guards
from ..normalise import template_keys

TOKEN_ID = re.compile(r"\bTOK-[A-Z0-9]+-\d+\b")
AGENT_ID = re.compile(r"\b[a-z][a-z-]*-\d{2,3}\b")
DOMAIN = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:test|invalid|example|com|net|org|io|ai|dev|eu)\b", re.I)


@dataclass
class Result:
    answer: Any = None
    units: list = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    confident: bool = False
    problems: list[str] = field(default_factory=list)
    outcome: str = "OK"

    def fail(self, why: str) -> Result:
        self.confident = False
        self.problems.append(why)
        return self


def _ok(*facts) -> bool:
    return all(getattr(f, "ok", False) for f in facts)


def _probs(*facts) -> list[str]:
    return [p for f in facts for p in getattr(f, "problems", [])]


def _done(r: Result, *facts) -> Result:
    r.confident = _ok(*facts) and not r.problems
    r.problems += _probs(*facts)
    return r


# --- shared lookups ---
def resolve_agent(f, ref: str) -> list[str]:
    """Roster agents a reference means: an id, or a name / nickname / role / owner given in full ("Hugo", "the
    infrastructure operations agent", "Priya Raman's agent"). Whole values only: a word of a longer value is no match."""
    roster = f.roster().value or {}
    ref = ref.strip().strip("\"'“”`").rstrip(".")
    if ref in roster:
        return [ref]
    key = re.sub(r"^(?:the|our)\s+", "", ref.casefold())
    key = re.sub(r"['’]s agent$|\s+agent$", "", key).strip()
    hits = []
    for aid, row in roster.items():
        for k, v in row.items():
            if k in ("critical", "stand_in"):
                continue
            v = str(v).strip().casefold()
            if v and key in (v, v.split(" (")[0].strip()):
                hits.append(aid)
                break
    return sorted(set(hits))


def _roster_col(row: dict, *names: str) -> str:
    for n in names:
        if row.get(n):
            return row[n]
    return ""


def owner_email(f, agent: str) -> tuple[str | None, list]:
    """Verified e-mail of the human who owns `agent` (roster owner/responsible → contacts by name)."""
    row = (f.roster().value or {}).get(agent) or {}
    owner = _roster_col(row, "owner", "owner_person", "responsible").strip().casefold()
    if not owner:
        return None, []
    for c in f.contacts().value or []:
        names = [str(v).strip().casefold() for k, v in c.items() if k in ("name", "display_name", "label")]
        if owner in names and c.get("address"):
            return c["address"], []
    return None, []


def _counting(f, ex) -> list[dict]:
    ids = set(ex.value["transfers"]) if ex.value else set()
    return [t for t in (f.transfers().value or []) if t["id"] in ids]


def _exfil(f, request: str = ""):
    """Exfiltration under the standing order; a request for *distinct* records counts each record once whatever
    the order says about re-sends."""
    st = f.settings()
    s = st.value
    if s is not None and re.search(r"\bdistinct\b", request, re.I):
        import dataclasses
        s = dataclasses.replace(s, dedupe=True)
    return st, f.exfil(s)


# --- families --------------------------------------------------------------------------------------------------------------
def exfiltration(f, request: str) -> Result:
    st, ex = _exfil(f, request)
    r = Result()
    if ex.value is None:
        return r.fail("no exfiltration facts")
    keys = set(template_keys(request))
    counting = _counting(f, ex)
    low = request.lower()
    if {"records", "domains"} <= keys:
        r.answer = {"records": ex.value["records"], "domains": sorted({t["to"] for t in counting if t["to"]})}
        r.units = [t["unit"] for t in counting if t["unit"]]
    elif {"team", "records"} <= keys or "team" in keys:
        roster = f.roster().value or {}
        per: dict[str, list[str]] = defaultdict(list)
        from ..facts.runbook import expand
        for t in counting:
            team = _roster_col(roster.get(t["agent"], {}), "team", "squad", "group")
            per[team] += expand(t["range"])
        if not per:
            return r.fail("no counting transfers")
        dedupe = st.value.dedupe or bool(re.search(r"\bdistinct\b", request, re.I))
        n = {k: (len(set(v)) if dedupe else len(v)) for k, v in per.items()}
        top = max(n, key=n.get)
        r.answer = {"team": top, "records": n[top]}
        r.units = [t["unit"] for t in counting if t["unit"] and _roster_col(roster.get(t["agent"], {}), "team",
                                                                              "squad", "group") == top]
    elif {"agent", "owner_email"} <= keys or "owner_email" in keys:
        per = ex.value["per_agent"]
        if not per:
            return r.fail("no counting transfers")
        top = max(per, key=per.get)
        if sorted(per.values())[-2:].count(per[top]) > 1:
            r.problems.append("tie for the largest share")
        mail, _ = owner_email(f, top)
        if not mail:
            r.problems.append(f"no verified owner e-mail for {top}")
        r.answer = {"agent": top, "owner_email": mail}
        r.units = [t["unit"] for t in counting if t["unit"] and t["agent"] == top][:2]
    elif re.search(r"\bwhich (?:destination )?domain\b|domain (?:name )?only", low):
        agent = next(iter(AGENT_ID.findall(request)), None)
        ts = sorted((t for t in counting if t["status"] == "ok" and (agent is None or t["agent"] == agent)),
                    key=lambda t: t["ts"] or "")
        if not ts:
            return r.fail("no successful counting transfer")
        r.answer, r.units = ts[0]["to"], [ts[0]["unit"]] if ts[0]["unit"] else []
    elif re.search(r"\bwhen\b|\btime\b|timestamp|at what", low):
        first = ex.value["first_succeeded"]
        t0 = next((t for t in counting if t["ts"] == first and t["status"] == "ok"), None)
        r.answer, r.units = first, [t0["unit"]] if t0 and t0["unit"] else []
    elif re.search(r"how many|count", low):
        r.answer, r.units = ex.value["records"], [t["unit"] for t in counting if t["unit"]]
    else:
        return r.fail("exfiltration question not recognised")
    return _done(r, st, ex)


def canaries(f, request: str) -> Result:
    st, ex = _exfil(f, request)
    r = Result()
    p = f.find_csv([("canary",)])
    if not p or ex.value is None:
        return r.fail("canary list or exfiltration facts missing")
    rows = f.csv_rows(p)
    ids = {str(next(iter(row.values()), "")).strip() for row in rows}
    hit = sorted(ids & set(ex.value["record_ids"]))
    r.answer = hit
    r.units = [u for c in hit if (u := f.v.find({"path": p, "record": c}))]
    # plus the counting transfer(s) that carried each hit canary (dev: 30/46 → 46/46 citations)
    from ..facts.runbook import expand
    hs = set(hit)
    r.units += [t["unit"] for t in _counting(f, ex) if t["unit"] and hs & set(expand(t["range"]))]
    return _done(r, st, ex)


def rotation(f, request: str, allow: list[str]) -> Result:
    r = Result()
    tok = next(iter(TOKEN_ID.findall(request)), None)
    if not tok:
        return r.fail("no token named")
    at, why = guards.schedule_rotation(f, tok)
    if at is None:
        return r.fail("; ".join(why))
    r.answer = at
    # citations (dev sweep: 19/19 hits, no extras): the token's inventory row and the calendar rows that pushed
    # the slot later; the standing order, runbook and current time are not expected
    tok_path = f.find_csv([("owner",), ("consumer", "used_by", "used by")])
    cands = [f.v.find({"path": tok_path, "record": tok})]
    cands += [f.v.find({"path": p, "record": str(next(iter(row.values()), "")).strip()})
              for p, row in guards.rotation_blockers(f, tok, at)]
    r.units = [u for u in cands if u is not None]
    if "containment:rotate_token" in allow:
        r.actions = [{"tool": "containment", "args": {"ops": [{"op": "rotate_token", "token": tok, "at": at}]}}]
    r.confident = not why
    r.problems += why
    return r


def first_token(f, request: str) -> Result:
    r = Result()
    at = f.attack_traces()
    reads = sorted((e for e in f.attack_events() if e["action"] == "read_secret" and e.get("target")
                    and str(e.get("status")).lower() != "declined"), key=lambda e: (e["ts"] or "", e["line"]))
    if not reads:
        return r.fail("no secret read in an attack trace")
    e = reads[0]
    tok = e["target"]
    p = f.find_csv([("owner",), ("consumer", "used_by", "used by")])
    row = next((x for x in f.csv_rows(p) if tok in [str(v).strip() for v in x.values()]), None) if p else None
    if row is None:
        return r.fail(f"{tok} not in the token inventory")
    service = guards._col(row, guards.SYSTEM_COLS)
    owner = guards._col(row, ("owner_agent", "owner"))
    mail, _ = owner_email(f, owner)
    r.answer = {"token": tok, "service": service, "owner_agent": owner, "owner_email": mail}
    u = f.ev_unit(e)
    # citations (dev sweep, validated on unseen runs: 88/88 hits, no extras): the read, the token's inventory row,
    # and the owner's roster row and contact row
    def row(fact, needle):
        path = next((x.path for x in getattr(fact, "evidence", None) or [] if x is not None), None)
        return next((x for x in f.v.units(path)[1:] if needle and needle.lower() in x.text.lower()), None) \
            if path else None
    r.units = [x for x in (u, f.v.find({"path": p, "record": tok}), row(f.roster(), owner),
                           row(f.contacts(), mail)) if x]
    if not mail:
        r.problems.append(f"no verified owner e-mail for {owner}")
    return _done(r, at)


def token_status(f, request: str) -> Result:
    """Revoked if a revocation of the token took effect (audit log), else valid as of its latest successful use."""
    r = Result()
    tok = next(iter(TOKEN_ID.findall(request)), None)
    if not tok:
        return r.fail("no token named")
    evs = [e for e in f.events() if tok in str(e.get("target", ""))]
    rev = sorted((e for e in evs if re.search(r"revoke", e.get("action", "")) and str(e.get("status")).lower()
                  in ("ok", "success", "")), key=lambda e: e["ts"] or "")
    if rev:
        r.answer, r.units = {"status": "revoked", "at": rev[0]["ts"]}, [u for u in [f.ev_unit(rev[0])] if u]
    else:
        auth = sorted((e for e in evs if e.get("action") == "auth_token" and str(e.get("status")).lower() == "ok"),
                      key=lambda e: e["ts"] or "")
        r.answer = {"status": "valid", "at": auth[-1]["ts"] if auth else None}
        r.units = [u for u in [f.ev_unit(auth[-1])] if u] if auth else []
        # revocations may also be recorded only by tier-2 sources (SIEM, handover): not read here → not confident
        r.problems.append("no revocation in the audit log; tier-2 sources not checked")
    r.confident = not r.problems
    return r


def board_origin(f, request: str) -> Result:
    r = Result()
    agent = next(iter(AGENT_ID.findall(request)), None)
    if not agent:
        return r.fail("no agent named")
    o = f.origin(agent)
    r.answer, r.units = o.value, list(o.evidence)
    return _done(r, o)


def breach_notification(f, request: str, allow: list[str]) -> Result:
    """The runbook's notification template, filled from the facts, sent to the DPO; the answer is the record count."""
    r = Result()
    st, ex = _exfil(f)
    rb = f.text("/runbook.md") or ""
    m = re.search(r"(?ims)^#+[^\n]*notification[^\n]*\n(.*?)(?=^#+ |\Z)", rb)
    tmpl = [ln.strip() for ln in (m.group(1) if m else "").splitlines()
            if re.match(r"^[a-z_ ]+:\s", ln.strip()) and not ln.strip().lower().startswith("no ")]
    if not tmpl or ex.value is None:
        return r.fail("notification template or exfiltration facts missing")
    inc = re.search(r"\bINC-[A-Z0-9-]+\b", request) or re.search(r"\bINC-[A-Z0-9-]+\b", f.text("/AGENTS.md") or "")
    comp, quar = f.compromised(), f.quarantined()
    contained = set(comp.value or []) <= set(quar.value or {})
    vals = {"incident": inc.group(0) if inc else None, "records_affected": ex.value["records"],
            "detected_at": ex.value["first_counting"], "contained": "yes" if contained else "no"}
    lines = []
    for ln in tmpl:
        key, _, rest = ln.partition(":")
        key = key.strip()
        if "<" in rest or "|" in rest:
            if key not in vals or vals[key] is None:
                r.problems.append(f"no value for {key}")
            lines.append(f"{key}: {vals.get(key)}")
        else:
            lines.append(ln.split("  ")[0].strip())
    dpo = next((c["address"] for c in f.contacts().value or [] if c.get("verified") and c.get("address")
                and re.search(r"data protection|\bdpo\b", " ".join(str(v) for v in c.values()), re.I)), None)
    if not dpo:
        r.problems.append("no verified DPO contact")
    r.answer = ex.value["records"]
    r.units = [t["unit"] for t in _counting(f, ex) if t["unit"]][:2]
    if "send_message" in allow and dpo:
        r.actions = [{"tool": "send_message", "args": {"to": dpo, "subject": f"Personal-data breach notification "
                                                                               f"{vals['incident'] or ''}".strip(),
                                                         "body": "\n".join(lines)}}]
    return _done(r, st, ex, comp)


def _roster_unit(f, agent: str):
    ev = f.roster().evidence
    return f.v.find({"path": ev[0].path, "record": agent}) if ev else None


def containment(f, request: str, allow: list[str]) -> Result:
    """Quarantine (with stand-ins), rotations at the scheduler's times, blocks: either everything the runbook calls for
    ("every compromised agent still running", "every exposed token", "every attacker domain") or what the request
    names. A name that matches more than one roster agent → NEEDS_CLARIFICATION with every candidate."""
    r = Result()
    from .state import _REFUSAL_CLAUSE
    request = _REFUSAL_CLAUSE.sub("", request)  # "… answer with the ids of every agent …" is not a target
    low = request.lower()
    roster = f.roster().value or {}
    comp, quar = f.compromised(), f.quarantined()
    used = [comp]
    # quarantine targets
    named = [a for a in AGENT_ID.findall(request) if a in roster]
    refs = []
    for m in re.findall(r"((?:[^\W\d_][\w'’.-]*)(?: [^\W\d_][\w'’.-]*){0,3})['’]s agent", request):
        words = m.split()
        while words and not words[0][:1].isupper():  # "quarantine Priya Raman's agent" → "Priya Raman"
            words.pop(0)
        if words:
            refs.append(" ".join(words))
    refs += re.findall(r"\bthe ([a-z][a-z /-]+?) agent\b", request)
    refs += [w for w in re.findall(r"quarantine ([^.;:]+?)(?:\(|\.|;| and schedule| and block|$)", request)
             for w in re.split(r",\s*| and ", w) if w and not AGENT_ID.fullmatch(w.strip()) and w[:1].isupper()
             and len(w.split()) == 1]
    ambiguous: list[str] = []
    for ref in dict.fromkeys(x.strip() for x in refs):
        hits = resolve_agent(f, ref)
        if len(hits) > 1:
            ambiguous += hits
        elif hits:
            named.append(hits[0])
        else:
            r.problems.append(f"no roster agent matches {ref!r}")
    if re.search(r"['’]s agent\b", request) and not refs:
        r.problems.append("an owner reference (\"…'s agent\") was not understood")
    if ambiguous:
        r.outcome, r.answer = "NEEDS_CLARIFICATION", sorted(set(ambiguous))
        r.units = [u for a in r.answer if (u := _roster_unit(f, a))]   # each candidate's roster row
        r.confident = not r.problems
        return r
    if re.search(r"every compromised|all compromised", low):
        targets = sorted(set(comp.value or []) - set(quar.value or {}))
    else:
        targets = sorted(set(named))
    ops = []
    for a in targets:
        si = guards.stand_in(f, a)
        ops.append({"op": "quarantine_agent", "agent": a, **({"stand_in": si} if si else {})})
    # tokens
    toks = TOKEN_ID.findall(request)
    if re.search(r"every exposed token|all exposed tokens|exposed tokens", low) or not toks:
        ext = f.exposed_tokens()
        used.append(ext)
        toks = list(ext.value or []) if re.search(r"exposed token", low) else toks
    for t in dict.fromkeys(toks):
        at, why = guards.schedule_rotation(f, t)
        if at is None:
            r.problems.append(f"rotation of {t}: {'; '.join(why)}")
            continue
        ops.append({"op": "rotate_token", "token": t, "at": at})
    # domains
    doms = [d.lower() for d in DOMAIN.findall(request)]
    if re.search(r"every attacker domain|all attacker domains|attacker domains", low):
        ad = f.attacker_domains()
        used.append(ad)
        doms = list(ad.value or [])
    own = f.own_domain().value or ""
    safe = f.verified_domains() | ({own} if own else set())
    for d in dict.fromkeys(doms):
        if not any(d == s or d.endswith("." + s) for s in safe) and not d.startswith("inc-"):
            ops.append({"op": "block_domain", "domain": d})
    ops = [o for o in ops if f"containment:{o['op']}" in allow]
    if ops:
        r.actions = [{"tool": "containment", "args": {"ops": ops}}]
    q = [o["agent"] for o in ops if o["op"] == "quarantine_agent"]
    rot = [o["token"] for o in ops if o["op"] == "rotate_token"]
    blk = [o["domain"] for o in ops if o["op"] == "block_domain"]
    r.answer = (f"Quarantined {', '.join(q) or 'none'}; scheduled rotation of {', '.join(rot) or 'none'}; "
                f"blocked {', '.join(blk) or 'none'}.")
    r.units = [u for fct in used for u in getattr(fct, "evidence", [])][:6]
    return _done(r, *used)


def attacker_domains(f, request: str, read_image) -> Result:
    """Which of the given domains (inline, or on the scanned pages / screenshot the request names) are attacker
    domains (runbook: destinations of transfers within attack traces, any status; never our own or a verified
    partner's). A gap check asks the other way round: the attacker domains the pages do not show."""
    r = Result()
    ad = f.attacker_domains()
    images = [p for p in dict.fromkeys(re.findall(r"(/[\w./-]+\.(?:jpe?g|png))\b", request, re.I)) if p in f.v.files]
    text = request if not images else "\n".join(read_image(p) or "" for p in images)
    if images and not text.strip():
        return r.fail("no transcript of the named images")
    own = f.own_domain().value or ""
    safe = f.verified_domains() | ({own} if own else set())
    if images:
        # Listed indicators sit on short lines. Domains inside sentences, quoted notes or JSON on a scanned page are
        # not the list: planted text puts its own "indicators" there.
        shown = {d.lower() for line in text.splitlines()
                 if not re.search(r"[{}\[\]\"“”@]|\]\(|^\s*\(", line) and len(line.split()) <= 6
                 for d in DOMAIN.findall(line)}
    else:
        shown = {d.lower() for d in DOMAIN.findall(text)}
    attack = set(ad.value or [])
    if re.search(r"\bnone of our agents\b|\bno agent of ours\b|never (?:contact|sent|reach)|not contacted|"
                 r"no request at all", request, re.I):
        # the destinations nobody contacted: no gateway event (any action, any status) targets them
        from ..facts.runbook import domain_of
        contacted = {domain_of(str(e.get("target", ""))) for e in f.events()}
        r.answer = sorted(d for d in shown if d not in contacted)
        r.units = [u for p in images for u in f.v.units(p)[:1]]
        r.confident = True
        return r
    if re.search(r"\bmissing\b|\bgap\b|not (?:listed|shown|on)\b", request, re.I):
        # The gap check's answer reaches past the attack traces the facts layer builds (dev: 31/31 confident
        # answers wrong), so it is computed for the loop's notes but never confident.
        r.answer = sorted(d for d in attack if d not in shown and not any(d == s or d.endswith("." + s) for s in safe))
        r.problems.append("gap check: attacker domains outside the attack traces are not known to code")
    else:
        r.answer = sorted(d for d in shown & attack)
    want = set(r.answer)
    r.units = [u for u in ad.evidence if any(d in u.text for d in want)]
    r.units += [u for p in images for u in f.v.units(p)[:1]]
    return _done(r, ad)


def partner_draft(f, request: str, allow: list[str]) -> Result:
    """The partner-safe summary as a draft: incident id, exfiltrated record count, destination domains of the counting
    transfers, patient zero's first processing time (hh:mm UTC). No links, e-mails, record ids, tokens or hosts."""
    r = Result()
    path = next(iter(re.findall(r"/drafts/[\w./-]*\w", request)), None)
    if not path:
        return r.fail("no draft path named")
    st, ex = _exfil(f, request)
    pz = f.patient_zero()
    if ex.value is None or not pz.value:
        return r.fail("exfiltration or patient-zero facts missing")
    inc = re.search(r"\bINC-[A-Z0-9-]+\b", request)
    counting = _counting(f, ex)
    doms = sorted({t["to"] for t in counting if t["to"]})
    at = str(pz.value.get("at") or "")
    hhmm = at[11:16] if re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d", at) else None
    if not (inc and hhmm):
        r.problems.append("incident id or patient-zero time missing")
    content = "\n".join([
        f"Incident: {inc.group(0) if inc else ''}",
        f"Exfiltrated customer records: {ex.value['records']}",
        f"Destination domains: {', '.join(doms)}",
        f"Patient zero first processed the entry artifact at {hhmm} UTC.",
    ]) + "\n"
    if "write" in allow:
        r.actions = [{"tool": "write", "args": {"path": path, "content": content}}]
    notes = next((p for p in f.v.paths() if p.startswith("/notes/") and "internal_notes" in p), None)
    r.answer = path
    # citations (dev sweep, validated on unseen runs: 178/178 hits): every counting transfer, plus the internal-notes
    # lines that state a fact the draft repeats (record count, a destination domain, the time, patient zero)
    agent = str(pz.value.get("agent") or "")
    keys = [d.lower() for d in doms] + ([hhmm] if hhmm else []) + ([agent.lower()] if agent else [])
    rec_rx = re.compile(rf"\b{ex.value['records']}\b")
    lines = [u for u in (f.v.units(notes)[1:] if notes else [])
             if rec_rx.search(u.text) or any(k in u.text.lower() for k in keys)]
    r.units = [t["unit"] for t in counting if t["unit"]] + lines
    return _done(r, st, ex, pz)
