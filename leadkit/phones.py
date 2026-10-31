"""Phone checks: is it a real number, is it a cell, and is it theirs?

Two layers. The free one formats every number and drops anything that
cannot be a US phone number, and (in the pipeline) marks numbers shared by
several agents as office lines. The paid one is a reverse skip trace
through Monid: whose record the number is on, and the line type (Wireless,
Landline, VoIP) that record gives it. A number the trace doesn't know comes
back "unknown", and the fix step looks the agent up by email or name.
"""
from __future__ import annotations

import re
from collections import Counter

import phonenumbers

from leadkit import settings

# Names that say nothing about who owns the number: carrier placeholders,
# and a bare city and state ("BOCA RATON FL").
_GENERIC_CNAM = re.compile(r"WIRELESS|CALLER|CELL|PHONE|MOBILE|UNAVAILABLE|UNKNOWN|PRIVATE")
_STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE "
              "NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR".split())
_LINE_WORDS = {"landline": "a landline", "voip": "a VoIP line", "toll-free": "a toll-free number"}


class PhoneCheckError(RuntimeError):
    pass


def normalize(raw: str | None, region: str = "US") -> str | None:
    """E.164 (+19545551234) if the number can exist, else None."""
    if not raw:
        return None
    try:
        num = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(num):
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def pretty(e164: str | None) -> str:
    if not e164:
        return ""
    return phonenumbers.format_number(phonenumbers.parse(e164), phonenumbers.PhoneNumberFormat.NATIONAL)


def configured() -> bool:
    return bool(settings.MONID_API_KEY)


def lookup_price() -> float:
    """At most this per number: a trace that finds nobody is free."""
    return settings.PRICES["skip_trace"]


_UNKNOWN = {"valid": True, "type": "unknown", "carrier": None, "caller_name": None, "caller_type": None}


def lookup(e164: str, usage=None) -> dict:
    """{valid, type, carrier, caller_name, caller_type, trace}: one number
    through a reverse skip trace. The number only counts as known when it's
    on the record the trace returns: asked about a number, it sometimes
    answers with a namesake who doesn't have it. Raises PhoneCheckError on a
    bad key or no funds, so a run stops instead of marking thousands of
    numbers as failed."""
    from leadkit import monid, skiptrace
    try:
        person = skiptrace.run(usage and (lambda _, calls, dollars: usage("phone", calls, dollars)),
                               phone=pretty(e164))
    except monid.MonidError as e:
        raise PhoneCheckError(str(e)) from e
    kinds = dict((person or {}).get("phones", []))
    if e164 not in kinds:
        return dict(_UNKNOWN)
    return {"valid": True, "type": kinds[e164], "carrier": None, "caller_name": person["name"] or None,
            "caller_type": None, "trace": person}


def _letters(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def name_match(caller_name: str | None, first: str, last: str) -> str:
    """'match', 'mismatch' or 'unknown' between a caller ID name and the agent.

    Caller ID names are uppercase, often last-name-first, and cut at 15
    characters ("DEUTSCH ADRIANA", "STEIN POSNER RE"), so a word that is the
    start of their last name counts. Carrier placeholders ("WIRELESS CALLER",
    "MIAMI FL") say nothing and come back unknown.
    """
    cnam = (caller_name or "").upper().strip()
    if not cnam or _GENERIC_CNAM.search(cnam) or cnam.split()[-1] in _STATES:
        return "unknown"
    words = [_letters(w) for w in re.split(r"[\s,]+", cnam) if len(_letters(w)) >= 3]
    last_l, first_l = _letters(last), _letters(first)
    for w in words:
        if last_l and (last_l.startswith(w) or w.startswith(last_l)):
            return "match"
        if first_l and len(first_l) >= 4 and w == first_l:
            return "match"
    return "mismatch"


def problem(agent: dict, shared: set[str]) -> str | None:
    """Why this agent's number probably isn't their own cell, or None."""
    if agent.get("phone_status") == "invalid_format":
        return "not a real number"
    e164 = agent.get("phone_e164")
    if not e164:
        return "no number" if not agent.get("phone") else None
    if e164 in shared:
        return "shared with other agents (office line)"
    if agent.get("phone_name_match") == "mismatch":
        return f"registered to {agent.get('phone_caller_name')}"
    if agent.get("phone_type") == "unknown":
        return "couldn't confirm it's their number"
    if agent.get("phone_type") and agent["phone_type"] != "mobile":
        return _LINE_WORDS.get(agent["phone_type"], f"a {agent['phone_type']} line")
    return None


# Which agents the phone steps cover. Phone lookups are the biggest cost on
# a big list, so a job can spend them only on agents it will contact.
PHONE_SCOPE = {
    "all": "1=1",
    "emails": "email IS NOT NULL AND COALESCE(email_check, 'ok') IN ('ok', 'catch_all')",
    "producers": "COALESCE(seller_deals_year, zillow_sales_year) >= ?",
}


def scope_sql(job) -> tuple[str, tuple]:
    where = PHONE_SCOPE.get(job.phones_for, "1=1")
    return where, ((job.producer_min,) if "?" in where else ())


def to_fix(job, store) -> tuple[list[dict], set[str]]:
    """(agents whose number needs fixing, each with a "reason"; every number
    already on someone in the job). The same list prices the fix, reports
    it, and runs it, so all three agree."""
    everyone = store.agents()
    counts = Counter(a["phone_e164"] for a in everyone if a["phone_e164"])
    shared = {n for n, c in counts.items() if c >= 2}
    where, params = scope_sql(job)
    in_scope = {a["key"] for a in store.agents(where, params)}
    out = []
    for a in everyone:
        reason = problem(a, shared)
        # An agent whose number was never checked hasn't been judged yet.
        checked = a["phone_status"] is not None or not a["phone"]
        if reason and checked and a["phone_fix"] is None and a["key"] in in_scope:
            out.append({**a, "reason": reason})
    return out, set(counts)


def kind_of_problem(reason: str) -> str:
    """Group the reasons for a one-line summary."""
    if reason.startswith("shared"):
        return "office lines shared by several agents"
    if reason.startswith("registered to") or reason.startswith("business"):
        return "registered to someone else"
    if reason.startswith("couldn't confirm"):
        return "not confirmed as theirs"
    if reason == "no number":
        return "no number at all"
    if reason == "not a real number":
        return "not real numbers"
    return reason.replace("a ", "", 1) + "s" if reason.startswith("a ") else reason
