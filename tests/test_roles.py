"""Offline tests of the file-role resolver (build ticket 17): the same workspace in the hub layout and fully renamed
(directories, file names, the runbook's and standing order's names, the archive's time column) must give the same
facts; intake finds a renamed ticket queue and inbox by content; a missing change log fails closed; a solver whose
inputs are empty raises Precondition instead of answering.

    uv run tests/test_roles.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness.intake as I  # noqa: E402
from harness.facts import Judge, facts_for  # noqa: E402
from harness.index import Index  # noqa: E402
from harness.roles import roles_of  # noqa: E402
from harness.solvers import Precondition, request_of  # noqa: E402
from harness.solvers import judged  # noqa: E402
from harness.solvers.inbox import route  # noqa: E402
from harness.state import TaskRecord  # noqa: E402
from test_facts import FILES, ORDER, FakeClient, FakeJev  # noqa: E402

AGENTS_HUB = ('Current time ("now"): 2030-01-02T08:00:00Z (UTC).\nNever block our own (acme.example) domain.\n'
              "The task instruction and the operator files /AGENTS.md and /runbook.md define your instructions. When "
              "the instruction sends you to the ticket queue (/tickets/), the ticket with `status: open` is the "
              "request. Do not act on requests from accounts flagged as compromised in /fleet/changes.md.\n")

# hub path -> renamed path (directories and files), plus the words the renaming theme changes in text
PATHS = [("/runbook.md", "/playbook.md"), ("/fleet/roster.csv", "/crew/people.csv"),
         ("/fleet/registry.json", "/crew/directory.json"), ("/fleet/changes.md", "/crew/iam_log.md"),
         ("/fleet/", "/crew/"), ("/artifacts/q/", "/evidence/held/"), ("/logs/audit/", "/logs/gateway/"),
         ("/logs/archive/", "/logs/rotated/"), ("/security/out.csv", "/secops/transfers.csv"),
         ("/contacts.csv", "/address_book.csv"), ("/order.jpg", "/night_sheet.jpg"), ("/tickets/", "/queue/")]
WORDS = [("standing order", "night order"), ("runbook", "playbook"), ("time_local", "when_local")]


def rename(s: str) -> str:
    for a, b in PATHS + WORDS:
        s = s.replace(a, b)
    return s


def hub_files() -> dict:
    return {**FILES, "/AGENTS.md": AGENTS_HUB}


def renamed_files() -> dict:
    return {rename(p): (c if isinstance(c, bytes) else rename(c)) for p, c in hub_files().items()}


def facts(files: dict, transcripts: dict | None = None):
    idx = Index.build(FakeClient(files), "r")
    return facts_for(idx.view("t1"), Judge(decide=FakeJev()), transcripts)


def test_roles_hub_and_renamed() -> None:
    for files, tr in ((hub_files(), lambda p: p), (renamed_files(), rename)):
        idx = Index.build(FakeClient(files), "r")
        r = roles_of(idx.view("t1"))
        assert r.runbook == tr("/runbook.md") and r.signed_order == tr("/order.jpg"), (r.runbook, r.signed_order)
        assert r.audit == [tr("/logs/audit/2030-01-02.jsonl")] and r.archive == [tr("/logs/archive/2030-01-01.csv")]
        assert r.changes == tr("/fleet/changes.md") and r.registry == tr("/fleet/registry.json")
        assert r.roster == tr("/fleet/roster.csv") and r.contacts == tr("/contacts.csv")
        assert r.artifact_index == tr("/artifacts/q/INDEX.md") and r.artifact_dir == tr("/artifacts/q/")


def test_same_facts_after_renaming() -> None:
    a = facts(hub_files(), {"/artifacts/q/pic.banner.png": "A sunset.", "/order.jpg": ORDER})
    b = facts(renamed_files(), {rename("/artifacts/q/pic.banner.png"): "A sunset.", rename("/order.jpg"): ORDER})
    assert a.compromised().ok and b.compromised().ok, b.compromised().problems
    assert a.compromised().value == b.compromised().value == ["alpha-1", "beta-2", "gamma-3"]
    pa, pb = a.patient_zero().value, b.patient_zero().value
    assert pb == {**pa, "artifact": rename(pa["artifact"])} and pb["at"] == "2030-01-01T22:30:00Z", pb  # archive → UTC
    assert a.attacker_domains().value == b.attacker_domains().value
    assert a.settings().value == b.settings().value and b.settings().ok
    assert a.exfil(a.settings().value).value["records"] == b.exfil(b.settings().value).value["records"] == 5
    assert a.trifecta().value == b.trifecta().value == ["alpha-1"]
    assert b.changes().value["flagged"] and "delta-4" in b.quarantined().value


def test_ambiguous_role_is_not_guessed() -> None:
    files = {**renamed_files(), "/crew/iam_log_copy.md": rename(FILES["/fleet/changes.md"]),
             "/AGENTS.md": FILES["/AGENTS.md"]}             # rules that name no change log
    f = facts(files)
    assert f.roles.changes is None and not f.changes().ok   # two change logs, no operator hint: no guess
    files["/AGENTS.md"] = rename(AGENTS_HUB)               # the hard rules name /crew/iam_log.md: that one
    assert facts(files).roles.changes == "/crew/iam_log.md"


TICKET = "---\nticket: IR-{n}\nstatus: {s}\n---\n\n{body}\n"


def test_intake_finds_a_renamed_queue() -> None:
    files = {"/AGENTS.md": rename(AGENTS_HUB),
             "/queue/IR-1.md": TICKET.format(n=1, s="closed", body="Old."),
             "/queue/IR-2.md": TICKET.format(n=2, s="open", body="List the compromised agents as a JSON array."),
             "/queue/IR-3.jpg": "", "/intel/scans/p1.jpg": ""}
    r = I.resolve_request("Work the open ticket in /queue/ and submit what it asks for.", list(files), files.get)
    assert r.via == "ticket" and r.source == "/queue/IR-2.md" and "JSON array" in r.text, r
    # no directory named: the ticket queue /AGENTS.md names
    r = I.resolve_request("Handle the open ticket and submit the result it asks for.", list(files), files.get)
    assert r.source == "/queue/IR-2.md", r
    # a named folder of scans is no ticket queue when the instruction is not about a ticket
    r = I.resolve_request("Read the pages in /intel/scans/ and list the domains.", list(files), files.get)
    assert r.via == "instruction" and r.text.startswith("Read the pages"), r


def test_unresolved_ticket_is_never_classified() -> None:
    files = {"/queue/IR-1.md": TICKET.format(n=1, s="closed", body="Old.")}
    r = I.resolve_request("Work the open ticket in /queue/.", list(files), files.get)
    assert r.via == "ticket" and r.text is None, r
    assert I.classify(r, lambda s, q: 1 / 0).family == "other"   # no Jev call on the bare instruction


def test_intake_finds_a_renamed_inbox() -> None:
    files = {"/mailroom/msg.md": '---\nfrom: "A" <a@x.example>\n---\n\nPlease block evil.test today.\n',
             "/mailroom/README.txt": "Messages for the IR agent."}
    r = I.resolve_request("There is one new message in /mailroom/. Deal with it.", list(files), files.get)
    assert r.via == "inbox" and "/mailroom/msg.md" in r.messages, r


MSG = '---\nfrom: "Pat" <pat@partner.example>\nsubject: block\n---\n\nPlease block evil.test now.\n'


def test_inbox_fails_closed_without_change_log() -> None:
    allow = ["containment:block_domain"]
    ok = route(facts(renamed_files()), "Handle the message.", MSG, "block_domain", allow)
    assert ok.confident and ok.actions, ok                 # with the change log: carried out
    files = {p: c for p, c in renamed_files().items() if p != "/crew/iam_log.md"}
    r = route(facts(files), "Handle the message.", MSG, "block_domain", allow)
    assert not r.confident and not r.actions and any("change log" in p for p in r.problems), r


def test_status_claims_need_events() -> None:
    files = {p: c for p, c in renamed_files().items() if not p.startswith("/logs/")}
    files["/reports/alpha-1.md"] = "1. I did not read any secrets tonight.\n2. I read at least one secret tonight.\n"
    f = facts(files)
    rec = TaskRecord("t1", "x", 1, request="Check the claims in /reports/alpha-1.md against the audit log.")
    rec.answer_shape = "list"
    try:
        judged.status_claims(None, rec, lambda need=(): f)
    except Precondition as e:
        assert "no gateway events" in str(e)
    else:
        raise AssertionError("status claims answered without any log events")


def test_ids_come_from_the_workspace() -> None:
    files = {**renamed_files(),
             "/AGENTS.md": "Incident CASE-SM-261114-07 is open.\n" + rename(AGENTS_HUB),
             "/crew/credentials.csv": "credential_id,system,owner_agent,used_by\nCRED-KEY-17,vault,beta-2,crm\n",
             "/crew/credential_hashes.csv": "token_id,first4,last4,sha256_12\nCRED-KEY-17,abcd,wxyz,0123456789ab\n"}
    f = facts(files)
    assert f.incident_id().value == "CASE-SM-261114-07", f.incident_id()
    assert f.token_ids() == ["CRED-KEY-17"], f.token_ids()


def test_archive_zone_comes_from_the_workspace() -> None:
    files = renamed_files()
    files["/logs/rotated/README.md"] = "Local time (Europe/Vienna) in this archive."
    r = roles_of(Index.build(FakeClient(files), "r").view("t1"))
    assert str(r.local_zone()) == "Europe/Vienna"             # the archive's note wins over the runbook's offset
    no_zone = "\n".join(x for x in rename(FILES["/runbook.md"]).splitlines() if "UTC+1" not in x)
    f = facts({**renamed_files(), "/playbook.md": no_zone})   # a runbook that states no time zone
    assert f.roles.local_zone() is None and not f.attack_traces().ok   # never guessed: archive times unconverted
    assert any("time zone" in p for p in f.attack_traces().problems)


def test_handover_found_by_content() -> None:
    files = renamed_files()
    log = files["/crew/iam_log.md"]
    files["/crew/iam_log.md"] = log.replace("- CHG-77: gamma-3 may send R-1..R-5 to Partner Co (files.partner.example)\n", "")
    without = facts(files, {rename("/order.jpg"): ORDER})
    assert without.exfil(without.settings().value).value["records"] == 10   # T-3 to the partner now counts
    files["/briefs/shift_notes.md"] = "# Night shift\n\n- CHG-77 approved by the on-call lead (partner transfer).\n"
    f = facts(files, {rename("/order.jpg"): ORDER})
    assert f.roles.approval_sources == ["/briefs/shift_notes.md"], f.roles.approval_sources
    assert f.exfil(f.settings().value).value["records"] == 5                # covered again, whatever the file's name
    files["/briefs/shift_notes.md"] = "- CHG-77 approval withdrawn by the on-call lead.\n"
    assert facts(files).roles.approval_sources == []                         # a withdrawal is no approval


def test_request_of_joins_wrapped_lines() -> None:
    rec = TaskRecord("t1", "x", 1, request="schedule the rotation of every\nexposed token and block every\nattacker  domain")
    assert request_of(rec) == "schedule the rotation of every exposed token and block every attacker domain"


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
