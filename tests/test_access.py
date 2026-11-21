"""Offline tests of wave-2 solver pieces (build ticket 11): access-log policy parsing, validity windows, shortest
delegation chain. No model calls, no workspace.

    uv run tests/test_access.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.solvers import Precondition  # noqa: E402
from harness.solvers.access import _valid, parse_access_policy, shortest_chain  # noqa: E402

POLICY = """# Access-log policy
A request is a violation only when ALL the following conditions hold:
1. Its scope_class is "private".
2. Its action is one of: "read", "write". All other actions are out of scope.
3. Its result is one of: "ok", "partial". All other results are out of scope.
4. Its actor starts with "external:". Internal actors are out of scope.
5. It has no valid ordinary change ticket in /logs/access/change_tickets.csv. A valid ticket is listed with status
   "approved", matches both actor and resource, and covers the request timestamp: valid_from inclusive, valid_until
   exclusive, and strictly before revoked_at if set.
6. It has no valid emergency authorization in /logs/access/emergency_windows.csv. An emergency row authorizes only its
   exact actor, resource and action, with status "approved", valid_from inclusive, valid_until exclusive and strictly
   before revoked_at if set.
All timestamps are UTC.
"""


def test_policy_clauses():
    p = parse_access_policy(POLICY)
    assert p["scope_class"] == ["private"] and p["action"] == ["read", "write"] and p["result"] == ["ok", "partial"]
    assert p["actor_prefix"] == ["external:"] and not p["ticket_empty"]
    assert p["ticket_register"] == "/logs/access/change_tickets.csv" and p["ticket_status"] == "approved"
    assert p["emergency_register"] == "/logs/access/emergency_windows.csv"


def test_policy_variants():
    p = parse_access_policy(POLICY.replace('4. Its actor starts with "external:". Internal actors are out of scope.',
                                           "4. Any actor is in scope, including internal actors."))
    assert p["actor_prefix"] is None
    lines = [ln for ln in POLICY.splitlines() if not ln.lstrip().startswith(("5.", "6.", '"approved"', "exclusive",
                                                                             "before", "exact"))]
    p = parse_access_policy("\n".join(lines) + "\n5. Its ticket field is empty. Any nonempty ticket authorizes it.\n")
    assert p["ticket_empty"] and p["ticket_register"] is None


def test_unknown_clause_goes_to_loop():
    try:
        parse_access_policy(POLICY + "7. Its src_ip is outside 10.0.0.0/8.\n")
    except Precondition:
        return
    raise AssertionError("an unknown clause must raise Precondition")


def test_validity_window():
    row = {"status": "approved", "valid_from": "2026-11-13T11:30:00Z", "valid_until": "2026-11-13T19:30:00Z",
           "revoked_at": "2026-11-13T15:30:00Z"}
    assert _valid(row, "2026-11-13T11:30:00Z", "approved")          # from inclusive
    assert not _valid(row, "2026-11-13T15:30:00Z", "approved")      # revoked: strictly before
    assert not _valid({**row, "revoked_at": ""}, "2026-11-13T19:30:00Z", "approved")   # until exclusive
    assert not _valid({**row, "status": "rejected"}, "2026-11-13T12:00:00Z", "approved")


def test_shortest_chain():
    g = {"a": {"scopes": set(), "delegates_to": {"c", "b"}}, "b": {"scopes": set(), "delegates_to": {"d"}},
         "c": {"scopes": set(), "delegates_to": {"d", "x"}}, "d": {"scopes": {"read:billing"}, "delegates_to": set()}}
    assert shortest_chain(g, "a", "read:billing") == ["a", "b", "d"]    # ties broken by id
    assert shortest_chain(g, "d", "read:billing") == ["d"]
    assert shortest_chain(g, "a", "send:email_external") == []
    assert shortest_chain(g, "q", "read:billing") == []                  # quarantined / unknown start


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
