"""Access and credential solvers: leaked tokens, access-log policy, access review, delegation chain, lethal trifecta
(built as wave 2, ticket 11).

Every rule comes from the task's own workspace: the fingerprint matching rule (sha256_12 of the full value), the
access-log POLICY.md clauses (parsed clause by clause; an unrecognised clause sends the task to the loop), the access
policy's three deny rules, the registry's scope classes and the current permission graph of the fact layer.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from ..facts.decode import reveal
from ..facts.runbook import Facts, parse_local
from ..index import Unit
from ..state import TaskRecord
from . import Precondition, Solution, note, register

PATH_RX = re.compile(r"(/[\w./-]+\.\w+)")


def _iso(x: str | None) -> str:
    x = (x or "").strip()
    return (parse_local(x) or x) if x else ""


def row_units(f: Facts, path: str) -> list[Unit]:
    """The csv's data-row units, in row order (index i = csv_rows(path)[i])."""
    return [u for u in f.v.units(path) if u.kind == "record"]


def _row(units: list[Unit], i: int) -> Unit | None:
    return units[i] if i < len(units) else None


def _list_shape(rec: TaskRecord) -> None:
    if rec.answer_shape not in ("list", "unknown"):
        raise Precondition(f"answer shape {rec.answer_shape}, expected a list")


def _src(units) -> list[dict]:
    out, seen = [], set()
    for u in units:
        if u is None:
            continue
        s = u.source() if isinstance(u, Unit) else u
        k = json.dumps(s, sort_keys=True)
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


# --- leaked tokens -----------------------------------------------------------------------------------------------------
CAND_RX = re.compile(r"[A-Za-z0-9_-]{8,}")


def _fingerprints(f: Facts, request: str) -> tuple[str, dict[str, tuple[str, Unit | None]]]:
    p = next((p for p in PATH_RX.findall(request or "") if p.endswith(".csv") and f.v.doc(p) is not None
              and "sha256" in (f.text(p) or "").split("\n", 1)[0].lower()), None) or f.find_csv([("sha256",)])
    if not p:
        raise Precondition("fingerprint csv (sha256 column) not found")
    rows = f.csv_rows(p)
    keys = list(rows[0]) if rows else []
    hcol = next((k for k in keys if k and "sha256" in k.lower()), None)
    idcol = next((k for k in keys if k and re.search(r"token|id", k, re.I) and k != hcol), keys[0] if keys else None)
    units = row_units(f, p)
    out = {}
    for i, r in enumerate(rows):
        h = (r.get(hcol) or "").strip().lower()
        if h and r.get(idcol):
            out[h] = (r[idcol].strip(), _row(units, i))
    if not out:
        raise Precondition("fingerprint csv empty")
    return p, out


def _leak_texts(f: Facts, request: str, fp_path: str) -> list[tuple[str, list[Unit]]]:
    """(path, units) of the leaked material: the files the request names (not the fingerprints) and, when the
    request mentions it, the agents board."""
    paths = [p for p in PATH_RX.findall(request or "") if p != fp_path and f.v.doc(p) is not None]
    if re.search(r"\bboard\b", request or "", re.I):
        paths += [p for p in f.v.paths() if p.startswith("/board/")]
    if not paths:
        paths = [p for p in f.v.paths() if re.search(r"paste|dump|leak", p, re.I)]
    return [(p, f.v.units(p)) for p in dict.fromkeys(paths)]


@register("leaked_tokens")
def leaked_tokens(ctx: Any, rec: TaskRecord, facts) -> Solution:
    _list_shape(rec)
    f: Facts = facts(())
    fp_path, fp = _fingerprints(f, rec.request or rec.instruction)
    texts = _leak_texts(f, rec.request or rec.instruction, fp_path)
    if not texts:
        raise Precondition("no leaked material found")
    hits: dict[str, list[Unit]] = {}
    for _, units in texts:
        stream: list[tuple[str, Unit]] = []
        for u in units:
            t = u.text
            if u.path.endswith(".jsonl"):
                try:
                    ev = json.loads(t)
                    t = " ".join(str(v) for v in ev.values() if isinstance(v, str))
                except ValueError:
                    pass
            t = reveal(t)
            stream += [(m, u) for m in CAND_RX.findall(t)]
        # a value can be wrapped over two lines or split over two posts: also try each adjacent pair joined
        cands = [(s, [u]) for s, u in stream]
        cands += [(a + b, [ua, ub]) for (a, ua), (b, ub) in zip(stream, stream[1:])]
        for s, us in cands:
            for v in {s, s.split("=", 1)[-1]}:
                h = hashlib.sha256(v.encode()).hexdigest()[:12]
                if h in fp:
                    hits.setdefault(h, us)
    answer = sorted({fp[h][0] for h in hits})
    srcs = _src([u for h in sorted(hits, key=lambda h: fp[h][0]) for u in hits[h] + [fp[h][1]]])
    notes = [note(f"{fp[h][0]}: full value in {us[0].path} matches sha256_12 {h}", us + [fp[h][1]])
             for h, us in hits.items()]
    return Solution(answer=answer, sources=srcs, confident=True, notes=notes)


# --- access-log policy -------------------------------------------------------------------------------------------------
def _quoted(s: str) -> list[str]:
    return [x.lower() for x in re.findall(r'"([^"]*)"', s)]


def parse_access_policy(text: str) -> dict:
    """POLICY.md → the conjunction of conditions. Raises Precondition on a numbered clause it doesn't know."""
    pol: dict[str, Any] = {"scope_class": None, "action": None, "result": None, "actor_prefix": None,
                           "ticket_empty": False, "ticket_register": None, "ticket_status": None,
                           "emergency_register": None, "emergency_status": None}
    for m in re.finditer(r"^\s*(\d+)\.\s+(.+?)(?=^\s*\d+\.\s|\Z)", text, re.M | re.S):
        c = " ".join(m.group(2).split())
        lc = c.lower()
        if lc.startswith("its scope_class is"):
            pol["scope_class"] = _quoted(c.split(".")[0])
        elif lc.startswith("its action is"):
            pol["action"] = _quoted(c.split(". ")[0])
        elif lc.startswith("its result is"):
            pol["result"] = _quoted(c.split(". ")[0])
        elif lc.startswith("its actor starts with"):
            pol["actor_prefix"] = _quoted(c.split(". ")[0])
        elif re.match(r"any actor is in scope", lc):
            pol["actor_prefix"] = None
        elif lc.startswith("its ticket field is empty"):
            pol["ticket_empty"] = True
        elif re.match(r"it has no valid (ordinary )?change ticket in", lc):
            pol["ticket_register"] = PATH_RX.search(c).group(1)
            pol["ticket_status"] = (_quoted(c) or ["approved"])[0]
            if not re.search(r"matches both actor and resource", lc):
                raise Precondition(f"ticket clause not recognised: {c[:120]}")
        elif re.match(r"it has no valid emergency authori[sz]ation in", lc):
            pol["emergency_register"] = PATH_RX.search(c).group(1)
            pol["emergency_status"] = (_quoted(c) or ["approved"])[0]
            if not re.search(r"exact actor, resource and action", lc):
                raise Precondition(f"emergency clause not recognised: {c[:120]}")
        else:
            raise Precondition(f"policy clause not recognised: {c[:120]}")
    if pol["scope_class"] is None or pol["action"] is None or pol["result"] is None:
        raise Precondition("policy lacks scope_class / action / result clauses")
    return pol


def _valid(row: dict, ts: str, status: str) -> bool:
    if (row.get("status") or "").strip().lower() != status:
        return False
    vf, vu, rv = _iso(row.get("valid_from")), _iso(row.get("valid_until")), _iso(row.get("revoked_at"))
    return bool(vf and vu) and vf <= ts < vu and (not rv or ts < rv)


def violations(f: Facts, log_path: str, pol: dict) -> tuple[list[tuple[str, Unit | None]], list[str]]:
    """Violating (request_id, row unit) and the registers used."""
    rows = f.csv_rows(log_path)
    if not rows or not {"request_id", "ts", "actor", "action", "scope_class", "result"} <= set(rows[0]):
        raise Precondition(f"{log_path} lacks the policy's columns")
    tickets = f.csv_rows(pol["ticket_register"]) if pol["ticket_register"] else []
    emerg = f.csv_rows(pol["emergency_register"]) if pol["emergency_register"] else []
    if pol["ticket_register"] and not tickets and f.v.doc(pol["ticket_register"]) is None:
        raise Precondition(f"{pol['ticket_register']} not found")
    if pol["emergency_register"] and not emerg and f.v.doc(pol["emergency_register"]) is None:
        raise Precondition(f"{pol['emergency_register']} not found")
    units = row_units(f, log_path)
    out = []
    for i, r in enumerate(rows):
        g = lambda k: (r.get(k) or "").strip()  # noqa: E731
        if g("scope_class").lower() not in pol["scope_class"] or g("action").lower() not in pol["action"]:
            continue
        if g("result").lower() not in pol["result"]:
            continue
        if pol["actor_prefix"] and not any(g("actor").lower().startswith(p) for p in pol["actor_prefix"]):
            continue
        if pol["ticket_empty"] and g("ticket"):
            continue
        ts = _iso(g("ts"))
        if pol["ticket_register"] and any(
                (t.get("actor") or "").strip() == g("actor") and (t.get("resource") or "").strip() == g("resource")
                and (t.get("ticket") or "").strip() == g("ticket")       # the request's own ticket
                and _valid(t, ts, pol["ticket_status"]) for t in tickets):
            continue
        if pol["emergency_register"] and any(
                (e.get("actor") or "").strip() == g("actor") and (e.get("resource") or "").strip() == g("resource")
                and (e.get("action") or "").strip().lower() == g("action").lower()
                and _valid(e, ts, pol["emergency_status"]) for e in emerg):
            continue
        out.append((g("request_id"), _row(units, i)))
    regs = [p for p in (pol["ticket_register"], pol["emergency_register"]) if p]
    return out, regs


@register("access_log")
def access_log(ctx: Any, rec: TaskRecord, facts) -> Solution:
    _list_shape(rec)
    f: Facts = facts(())
    req = rec.request or rec.instruction
    named = PATH_RX.findall(req)
    pol_path = next((p for p in named if re.search(r"polic", p, re.I)), None) or \
        next((p for p in f.v.paths() if p.startswith("/logs/access/") and re.search(r"polic", p, re.I)), None)
    log_path = next((p for p in named if p.endswith(".csv") and p != pol_path), None) or \
        next((p for p in f.v.paths() if p.startswith("/logs/access/") and "request" in p.lower()), None)
    if not pol_path or not log_path or f.text(pol_path) is None:
        raise Precondition("access-log policy or request log not found")
    pol = parse_access_policy(f.text(pol_path))
    viol, regs = violations(f, log_path, pol)
    answer = sorted({rid for rid, _ in viol})
    srcs = [{"path": pol_path}] + _src([u for _, u in viol])
    if re.search(r"register", req, re.I):
        srcs += [{"path": p} for p in regs]
    notes = [note(f"{rid} violates {pol_path}", [u]) for rid, u in viol[:20]]
    return Solution(answer=answer, sources=srcs, confident=True, notes=notes)


# --- permission graph with provenance ------------------------------------------------------------------------------------
def scope_classes(f: Facts) -> dict[str, str]:
    reg = f.registry().value or {}
    classes = reg.get("scope_classes") or {}
    names = set(classes.values())
    need = {k: next((n for n in names if k in n.lower()), None) for k in ("private", "untrusted", "outbound")}
    if None in need.values():
        raise Precondition(f"scope classes {sorted(names)} lack private/untrusted/outbound")
    return {s: k for s, c in classes.items() for k, n in need.items() if c == n}


def trifecta_of(f: Facts, g: dict, cls: dict[str, str]) -> set[str]:
    return {a for a in g if {"private", "untrusted", "outbound"} <= {cls.get(s) for s in f.effective(g, a)}}


def perm_graph(f: Facts, current: bool) -> tuple[dict, dict]:
    """f.permissions(current) plus provenance: {("scope", agent, scope) | ("deleg", agent, target): unit} — the
    registry export (cited by path) or the change-log row / audit grant that made it."""
    perm = f.permissions(current)
    if not perm.ok or not perm.value:
        raise Precondition("; ".join(perm.problems[:3]) or "no permission graph")
    reg = f.registry().value
    reg_u = {"path": reg["path"]}
    prov: dict[tuple, Any] = {}
    for a in reg.get("agents", []):
        for sc in a.get("scopes") or []:
            prov[("scope", a.get("id"), sc)] = reg_u
        for b in a.get("delegates_to") or []:
            prov[("deleg", a.get("id"), b)] = reg_u
    if current:
        exported = parse_local(reg.get("exported_at", "")) or ""
        ops = []
        ch = f.changes().value
        for r in ch["rows"]:
            ops.append((r["time"] or "", r["change"], r["agent"], r["value"], {"path": ch["path"], "line": r["line"]}))
        for e in f.events():
            if e["action"] in ("grant_scope", "revoke_scope", "add_delegation", "remove_delegation") \
                    and str(e.get("status", "ok")).lower() == "ok":
                ops.append((e["ts"] or "", e["action"], e["agent"], e.get("target", ""), e))
        for ts, op, agent, value, where in sorted(ops, key=lambda o: o[:4]):
            if exported and ts and ts < exported:
                continue
            u = f.ev_unit(where) if "action" in where else f.v.find(where)
            prov.setdefault("ops", []).append((agent, value, u))
            if op == "grant_scope":
                prov[("scope", agent, value)] = u
            elif op == "add_delegation":
                prov[("deleg", agent, value)] = u
    return perm.value, prov


def permission_sources(f: Facts, prov: dict, current: bool) -> list[dict]:
    """What a permission answer cites: the registry export (by path) and, for current permissions, every change
    after the export (change-log rows, agents' own grants in the audit log)."""
    return _src([{"path": (f.registry().value or {}).get("path")}]
                + ([u for _, _, u in prov.get("ops", [])] if current else []))


def touching(f: Facts, prov: dict, agents: set[str]) -> list:
    """Post-export changes that involve `agents` (as subject or value), and the evidence of their quarantine."""
    out = [u for a, v, u in prov.get("ops", []) if a in agents or v in agents]
    q = f.quarantined()
    out += [_mentioning(q.evidence, a) for a in sorted(agents & set(q.value))]
    return out


def why_scope(f: Facts, g: dict, prov: dict, agent: str, scope: str) -> list[Unit | None]:
    """Evidence that `agent` reaches `scope`: the shortest delegation path's edges and the holder's grant."""
    chain = shortest_chain(g, agent, scope)
    if not chain:
        return []
    return [prov.get(("deleg", a, b)) for a, b in zip(chain, chain[1:])] + [prov.get(("scope", chain[-1], scope))]


def _current(request: str) -> bool:
    r = (request or "").lower()
    return not ("registry export" in r and not re.search(r"\bcurrent", r))


def _record(f: Facts, agent: str) -> Unit | None:
    """The agent's own record in the registry export (structured evidence naming it)."""
    reg = f.registry().value or {}
    return f.v.find({"path": reg.get("path"), "record": agent}) if reg.get("path") else None


def chain_records(f: Facts, g: dict, agent: str, cls: dict[str, str]) -> list:
    """Registry records of the agents through which `agent` reaches each trifecta class."""
    out = []
    for k in ("private", "untrusted", "outbound"):
        out += [_record(f, a) for a in shortest_chain(g, agent, _class_scope(f, g, agent, cls, k))]
    return out


def _class_scope(f: Facts, g: dict, agent: str, cls: dict[str, str], k: str) -> str:
    """One scope of class k that agent reaches (the nearest one, ties by name)."""
    best = None
    for sc in sorted(s for s, c in cls.items() if c == k):
        ch = shortest_chain(g, agent, sc)
        if ch and (best is None or len(ch) < best[0]):
            best = (len(ch), sc)
    return best[1] if best else ""


# --- lethal trifecta ---------------------------------------------------------------------------------------------------
@register("lethal_trifecta")
def lethal_trifecta(ctx: Any, rec: TaskRecord, facts) -> Solution:
    _list_shape(rec)
    req = rec.request or rec.instruction
    current = _current(req)
    f: Facts = facts(())
    cls = scope_classes(f)
    g, prov = perm_graph(f, current)
    answer = sorted(trifecta_of(f, g, cls))
    ev = {a: [u for k in ("private", "untrusted", "outbound")
              for u in why_scope(f, g, prov, a, _class_scope(f, g, a, cls, k))] for a in answer}
    srcs = permission_sources(f, prov, current)
    notes = [note(f"{a} has private data + untrusted input + outbound (effective)",
                  [_record(f, a)] + [u for u in ev[a] if isinstance(u, Unit)] + chain_records(f, g, a, cls))
             for a in answer]
    return Solution(answer=answer, sources=srcs, confident=True, notes=notes)


# --- delegation chain --------------------------------------------------------------------------------------------------
def shortest_chain(g: dict, start: str, scope: str) -> list[str]:
    if start not in g:
        return []
    prev, frontier, seen = {start: None}, [start], {start}
    while frontier:
        nxt = []
        for a in frontier:
            if scope in g[a]["scopes"]:
                chain = [a]
                while prev[chain[-1]] is not None:
                    chain.append(prev[chain[-1]])
                return chain[::-1]
            for b in sorted(g[a]["delegates_to"]):
                if b not in seen and b in g:
                    seen.add(b)
                    prev[b] = a
                    nxt.append(b)
        frontier = nxt
    return []


@register("delegation_chain")
def delegation_chain(ctx: Any, rec: TaskRecord, facts) -> Solution:
    _list_shape(rec)
    req = rec.request or rec.instruction
    f: Facts = facts(())
    reg = f.registry().value or {}
    scopes = set((reg.get("scope_classes") or {}))
    for a in reg.get("agents", []):
        scopes |= set(a.get("scopes") or [])
    ids = f.agent_ids()
    m = re.search(r"\bcan\s+([\w.-]+)\b", req, re.I)
    start = m.group(1) if m and m.group(1) in ids else None
    if start is None:
        named = [a for a in ids if re.search(rf"(?<![\w-]){re.escape(a)}(?![\w-])", req)]
        start = named[0] if len(named) == 1 else None
    sc = [s for s in scopes if re.search(rf"(?<![\w:-]){re.escape(s)}(?![\w:-])", req)]
    if not start or len(sc) != 1:
        raise Precondition(f"start agent {start!r} / scope {sc} not identified")
    current = _current(req)
    g, prov = perm_graph(f, current)
    chain = shortest_chain(g, start, sc[0])
    ev = why_scope(f, g, prov, start, sc[0])
    srcs = permission_sources(f, prov, current)
    notes = [note(f"delegation chain {start} → {sc[0]}: {chain or 'unreachable'}",
                  [_record(f, a) for a in chain] + [u for u in ev if isinstance(u, Unit)])]
    return Solution(answer=chain, sources=srcs, confident=True, notes=notes)


# --- access review -----------------------------------------------------------------------------------------------------
def _mentioning(units: list, agent: str):
    rx = re.compile(rf"(?<![\w-]){re.escape(agent)}(?![\w-])")
    return next((u for u in units if u is not None and rx.search(u.text)), None)


OWNER_COLS = ("owner", "owner_person", "responsible", "owner_name")


@register("access_review")
def access_review(ctx: Any, rec: TaskRecord, facts) -> Solution:
    if rec.answer_shape not in ("object", "unknown"):
        raise Precondition(f"answer shape {rec.answer_shape}, expected an object")
    req = rec.request or rec.instruction
    f: Facts = facts(("artifacts",))
    named = PATH_RX.findall(req)
    pend = next((p for p in named if p.endswith(".csv")), None) or \
        next((p for p in f.v.paths() if "pending" in p.lower() and p.endswith(".csv")), None)
    pol_path = next((p for p in named if p.endswith(".md")), None) or \
        next((p for p in f.v.paths() if p.startswith("/fleet/") and "access_policy" in p.lower()), None)
    pol = (f.text(pol_path) or "").lower() if pol_path else ""
    if not pend or not pol:
        raise Precondition("pending requests or access policy not found")
    rules = {"blocked": bool(re.search(r"quarantin|compromis", pol)), "owner": "owner" in pol,
             "trifecta": "trifecta" in pol}
    if not all(rules.values()):
        raise Precondition(f"access policy rules not recognised: {rules}")
    roster = f.roster()
    perm = f.permissions(True)
    comp = f.compromised()
    if not roster.ok or not perm.ok or not comp.ok:
        why = "; ".join((roster.problems + perm.problems + comp.problems)[:3])
        return Solution(confident=False, why=why or "facts not ok")
    cls = scope_classes(f)
    g = perm.value
    base = trifecta_of(f, g, cls)
    blocked = set(f.quarantined().value) | set(comp.value)
    rows = f.csv_rows(pend)
    units = row_units(f, pend)
    approve, deny, srcs, notes = [], [], [], []
    for i, r in enumerate(rows):
        g_ = lambda k: (r.get(k) or "").strip()  # noqa: E731
        rid, agent = g_("request_id"), g_("agent")
        if not rid or g_("status").lower() in ("withdrawn", "cancelled", "canceled", "closed", "rejected"):
            continue
        u = _row(units, i)
        why = None
        if agent in blocked:
            why = "quarantined or compromised"
        else:
            row = (roster.value or {}).get(agent) or {}
            owner = next((row[c] for c in OWNER_COLS if row.get(c)), None)
            if owner is None:
                raise Precondition(f"no roster owner for {agent}")
            if g_("filed_by").lower() != owner.lower():
                why = f"filed by {g_('filed_by')}, owner is {owner}"
            else:
                h = copy.deepcopy(g)
                node = h.setdefault(agent, {"scopes": set(), "delegates_to": set()})
                ch, val = g_("change").lower(), g_("value")
                if ch == "grant_scope":
                    node["scopes"].add(val)
                elif ch == "add_delegation":
                    if val in h:
                        node["delegates_to"].add(val)
                else:
                    raise Precondition(f"unknown requested change {ch!r}")
                new = trifecta_of(f, h, cls) - base
                if new:
                    why = f"new lethal trifecta for {sorted(new)}"
        (deny if why else approve).append(rid)
        if why:                       # the expected evidence: the denied requests (approvals are not cited)
            srcs.append(u)
        notes.append(note(f"{rid} ({agent}): {'deny: ' + why if why else 'approve'}", [u]))
    # plus the registry export (the permission graph) and the later changes that involve a denied request's agent
    srcs.append({"path": (f.registry().value or {}).get("path")})
    _, prov = perm_graph(f, True)
    srcs += touching(f, prov, {(r.get("agent") or "").strip() for r in rows
                               if (r.get("request_id") or "").strip() in deny})
    return Solution(answer={"approve": sorted(approve), "deny": sorted(deny)}, sources=_src(srcs),
                    confident=True, notes=notes)
