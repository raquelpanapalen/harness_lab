"""Answer normalisation and citations (SPEC §7, build ticket 06): deterministic code between compose and submit.

- JSON given as a string is parsed; numbers come back as numbers where the request asks for a number.
- Timestamps become `YYYY-MM-DDTHH:MM:SSZ` in UTC: ISO with an offset, a space separator or no zone (taken as UTC),
  and the CET archive's `dd.mm.yyyy hh:mm:ss` (Europe/Berlin local time).
- Lists lose duplicates and are sorted when the request says sorted (never when it asks for an order).
- Object keys are renamed to the keys the request's JSON template names (case and separator differences only).
- Citations: handles → the evidence units' locators; unknown handles dropped; the ticket or inbox message carrying the
  task left out unless the outcome is a refusal; capped at answer items + 2 (4 for single values and objects).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from datetime import timedelta

try:  # the system time-zone database; slim container images may not have it
    from zoneinfo import ZoneInfo
    _CET = ZoneInfo("Europe/Berlin")
except Exception:  # noqa: BLE001 - the workspace's local time is CET (UTC+1) per the runbook
    _CET = timezone(timedelta(hours=1))
_ISO = re.compile(r"^\s*(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d(?::\d\d(?:\.\d+)?)?)\s*(Z|[+-]\d\d:?\d\d|UTC)?\s*$", re.I)
_DMY = re.compile(r"^\s*(\d\d)\.(\d\d)\.(\d{4})[ T](\d\d):(\d\d)(?::(\d\d))?\s*$")
_KEY = re.compile(r'"([A-Za-z_][\w-]*)"\s*:')
_NUMBER_HINT = re.compile(r"<number>|<n>|\bn\b|<count>|<int", re.I)


def utc(ts: str) -> str | None:
    """A timestamp string in UTC `…Z` form, or None if it isn't one."""
    m = _ISO.match(ts)
    if m:
        date, time_, zone = m.groups()
        if len(time_) == 5:
            time_ += ":00"
        time_ = time_.split(".")[0]
        if not zone or zone.upper() in ("Z", "UTC"):
            return f"{date}T{time_}Z"
        z = zone if ":" in zone else f"{zone[:3]}:{zone[3:]}"
        dt = datetime.fromisoformat(f"{date}T{time_}{z}")
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    m = _DMY.match(ts)
    if m:
        d, mo, y, h, mi, s = m.groups()
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), tzinfo=_CET)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def template_keys(request: str) -> list[str]:
    """Keys of the JSON object template in the request (`{"agent": ..., "owner_email": ...}`)."""
    m = re.search(r"\{[^{}]*\"[^{}]*\}", request or "")
    return list(dict.fromkeys(_KEY.findall(m.group(0)))) if m else []


def _number_keys(request: str) -> set[str]:
    m = re.search(r"\{[^{}]*\"[^{}]*\}", request or "")
    if not m:
        return set()
    return {k for k, v in re.findall(r'"([A-Za-z_][\w-]*)"\s*:\s*([^,}]*)', m.group(0)) if _NUMBER_HINT.search(v)}


def _num(x: Any) -> Any:
    if isinstance(x, str) and re.fullmatch(r"\s*-?\d+(\.\d+)?\s*", x):
        return float(x) if "." in x else int(x)
    return x


def _ts(x: Any) -> Any:
    return (utc(x) or x) if isinstance(x, str) else x


def _canon(k: str) -> str:
    return re.sub(r"[\s_-]", "", k).casefold()


def _maybe_json(x: Any) -> Any:
    if isinstance(x, str):
        s = x.strip()
        if s[:1] in "[{" or s in ("null", "true", "false"):
            try:
                return json.loads(s)
            except ValueError:
                pass
    return x


_URLISH = re.compile(r"^\s*(?:[a-z][a-z0-9+.-]*://|www\.)", re.I)


def as_domain(x: Any) -> Any:
    """A URL (live or defanged, `hxxps://evil[.]example/x`) → its bare host name; anything else unchanged."""
    if not isinstance(x, str) or not _URLISH.match(x):
        return x
    host = re.sub(r"^\s*[a-z][a-z0-9+.-]*://", "", x.strip(), flags=re.I).replace("[.]", ".").replace("(.)", ".")
    host = re.split(r"[/?#]", host, 1)[0].rsplit("@", 1)[-1].split(":", 1)[0]
    return host.lower().strip(".") or x


def answer(value: Any, shape: str, request: str) -> Any:
    req = request or ""
    v = _maybe_json(value)
    if shape == "list" and isinstance(v, (str, int, float)) and v != "":
        v = [v]
    if shape == "number":
        v = _num(v)
    if isinstance(v, list):
        items = [_ts(_maybe_json(x)) for x in v]
        if re.search(r"\bdomains?\b", req, re.I):  # asked for domains: a URL item is its host (ward rp003 t20)
            items = [as_domain(x) for x in items]
        if re.search(r"\bnumbers?\b", req, re.I):
            items = [_num(x) for x in items]
        out: list = []
        for x in items:
            if x not in out:
                out.append(x)
        ordered = re.search(r"\b(?:order|chronolog|earliest first|chain|in sequence)", req, re.I)
        if re.search(r"\bsorted\b", req, re.I) and not ordered:
            try:
                out = sorted(out, key=lambda x: (isinstance(x, str), x))
            except TypeError:
                pass
        return out
    if isinstance(v, dict):
        keys = {_canon(k): k for k in template_keys(req)}
        nums = _number_keys(req)
        out_d = {}
        for k, x in v.items():
            k2 = keys.get(_canon(str(k)), k)
            x = _maybe_json(x)
            if isinstance(x, list):
                x = answer(x, "list", req)
            else:
                x = _ts(x)
            out_d[k2] = _num(x) if k2 in nums else x
        return out_d
    return _ts(v)


def citation_cap(value: Any) -> int:
    return len(value) + 2 if isinstance(value, list) else 4


def sources(handles: list[str], resolve, value: Any, outcome: str, carriers: set[str]) -> list[dict]:
    """Locators for the cited handles: known handles only, no duplicates, no carrier ticket / inbox message on an OK
    outcome, at most `citation_cap` of them in the order given."""
    out: list[dict] = []
    for h in handles:
        u = resolve(str(h))
        if u is None:
            continue
        if outcome == "OK" and u.path in carriers:
            continue
        s = u.source()
        if s not in out:
            out.append(s)
    return out[:citation_cap(value)]
