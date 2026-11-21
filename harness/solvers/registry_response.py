"""Registry entries for the incident-response and inbox solvers (built as wave 4 and inbox routing, tickets 13 and
12); the families themselves are computed in harness/solvers/response.py and harness/solvers/inbox.py. Each family is enabled per the development measurement
(harvester/eval_wave4.py; ticket records 12 and 13). Each asks the facts layer only for what it needs: the standing
order ("settings") and/or the artifact transcripts behind attack-trace facts ("artifacts").
"""
from __future__ import annotations

import json
from typing import Any

from . import inbox as IB
from . import response as W
from . import Solution, note, register

SETTINGS, ARTIFACTS, BOTH = ("settings",), ("artifacts",), ("artifacts", "settings")


def _solution(r: W.Result) -> Solution:
    sources: list[dict] = []
    for u in r.units:
        if u is not None and u.source() not in sources:
            sources.append(u.source())
    notes = []
    if not r.confident and r.answer is not None and r.units:
        notes = [note(f"Code computed (unverified: {'; '.join(r.problems)[:200]}): "
                      f"{json.dumps(r.answer, ensure_ascii=False)[:300]}", r.units[:4])]
    return Solution(answer=r.answer, sources=sources, actions=r.actions, confident=r.confident, notes=notes,
                    outcome=r.outcome, why="; ".join(r.problems)[:500])


def _req(rec: Any) -> str:
    return rec.request or rec.instruction


@register("exfiltration")
def exfiltration(ctx, rec, facts) -> Solution:
    return _solution(W.exfiltration(facts(SETTINGS), _req(rec)))


@register("canaries")
def canaries(ctx, rec, facts) -> Solution:
    return _solution(W.canaries(facts(SETTINGS), _req(rec)))


@register("breach_notification")
def breach_notification(ctx, rec, facts) -> Solution:
    return _solution(W.breach_notification(facts(BOTH), _req(rec), rec.allowlist))


@register("containment")
def containment(ctx, rec, facts) -> Solution:
    return _solution(W.containment(facts(BOTH), _req(rec), rec.allowlist))


@register("rotation")
def rotation(ctx, rec, facts) -> Solution:
    return _solution(W.rotation(facts(SETTINGS), _req(rec), rec.allowlist))


@register("first_token")
def first_token(ctx, rec, facts) -> Solution:
    return _solution(W.first_token(facts(ARTIFACTS), _req(rec)))


@register("token_status")
def token_status(ctx, rec, facts) -> Solution:
    return _solution(W.token_status(facts(SETTINGS), _req(rec)))


@register("board_origin")
def board_origin(ctx, rec, facts) -> Solution:
    return _solution(W.board_origin(facts(ARTIFACTS), _req(rec)))


@register("inbox")
def inbox(ctx, rec, facts) -> Solution:
    return _solution(IB.solve_inbox(ctx, rec, facts(BOTH)))


@register("attacker_domains")
def attacker_domains(ctx, rec, facts) -> Solution:
    from .. import vision
    return _solution(W.attacker_domains(facts(ARTIFACTS), _req(rec),
                                        lambda p: vision.of(ctx).transcribe(rec.task_id, p)))


@register("partner_draft")
def partner_draft(ctx, rec, facts) -> Solution:
    return _solution(W.partner_draft(facts(BOTH), _req(rec), rec.allowlist))
