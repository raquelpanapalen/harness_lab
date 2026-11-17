"""The fact layer (SPEC §5, build ticket 09): decoders, the instruction judgment and per-task facts computed by code.

    from harness.facts import Judge, facts_for, parse_standing_order
    f = facts_for(ctx.index.view(task_id), judge)
    f.compromised().value, f.patient_zero().value, f.exfil(parse_standing_order(transcript)).value
"""
from .decode import decode_runs, fold, reveal
from .judge import Judge, Verdict
from .world import Fact, Facts, Settings, domain_of, expand, facts_for, parse_local, parse_standing_order

__all__ = ["Fact", "Facts", "Judge", "Settings", "Verdict", "decode_runs", "domain_of", "expand", "facts_for", "fold",
           "parse_local", "parse_standing_order", "reveal"]
