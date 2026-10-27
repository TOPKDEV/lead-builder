"""Skip trace (AnyAPI, through Monid): who a phone number, email or name
belongs to, and every number on that person's record with its line type.

It's both the phone check and the source of real cells. Looked up by the
number on file, it says whose number that is and whether it's Wireless or a
Landline; when the number is the agent's, the same record holds their other
cells. A trace that finds nobody costs nothing.

What the real answers showed (2026-10-27): it always returns at most one
person, and by name it often returns the wrong one, a namesake in another
state, and charges for it. So a person from a name lookup only counts when
both names match the agent and they live (or lived) in the agent's state.
"""
from __future__ import annotations

import re
from collections import Counter

import phonenumbers
from phonenumbers import geocoder

from leadkit import settings
from leadkit.names import NICKNAMES, split_name

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "washington d.c.": "DC", "washington dc": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY",
    "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR",
}


def _line_type(label: str | None) -> str:
    t = (label or "").lower()
    if "wireless" in t or "mobile" in t or "cell" in t:
        return "mobile"
    if "voip" in t:
        return "voip"
    if "landline" in t:
        return "landline"
    return "unknown"


def parse(res: dict) -> dict | None:
    """The person in a trace's answer, or None. Answers look like
    {"output": {"found": true, "data": {"items": [{firstName, lastName,
    addressRegion, phones: {phone1, phone1Type, ...}, emails: {email1, ...},
    previousAddresses: [...]}]}}}."""
    out = res.get("output") if isinstance(res, dict) else None
    items = ((out or {}).get("data") or {}).get("items") if isinstance(out, dict) else None
    if not items or not isinstance(items[0], dict):
        return None
    p = items[0]
    from leadkit.phones import normalize
    nums = p.get("phones") or {}
    found = []
    for i in range(1, 11):
        e164 = normalize(nums.get(f"phone{i}"))
        if e164 and e164 not in (n for n, _ in found):
            found.append((e164, _line_type(nums.get(f"phone{i}Type"))))
    emails = [v.lower() for k, v in sorted((p.get("emails") or {}).items()) if isinstance(v, str) and "@" in v]
    states = [p.get("addressRegion")] + [a.get("addressRegion") for a in p.get("previousAddresses") or []
                                         if isinstance(a, dict)]
    return {"name": " ".join(x for x in (p.get("firstName"), p.get("lastName")) if x),
            "first": p.get("firstName") or "", "last": p.get("lastName") or "",
            "location": p.get("location") or "", "states": [s for s in dict.fromkeys(states) if s],
            "phones": found, "emails": emails}


def cells(person: dict | None, exclude=()) -> list[str]:
    """The person's Wireless numbers, in the order the record lists them
    (the first is usually the current one)."""
    return [n for n, t in (person or {}).get("phones", []) if t == "mobile" and n not in exclude]


def run(usage=None, **lookup) -> dict | None:
    """One trace by phone, email or name (+ "address": "City, ST")."""
    from leadkit import monid
    if not settings.MONID_API_KEY:
        return None
    res = monid.run("anyapi", "/person.skip_trace", {k: v for k, v in lookup.items() if v}, timeout=120)
    if (res.get("status") or "").upper() in ("FAILED", "BLOCKED"):
        raise RuntimeError(f"the skip trace didn't run ({res.get('status')})")
    person = parse(res)
    if usage:
        # Billed per person found; a trace that finds nobody is free.
        cost = (res.get("cost") or {}).get("value")
        usage("skiptrace", 1, float(cost) if cost is not None else
              (settings.PRICES["skip_trace"] if person else 0.0))
    return person


def _first(word: str) -> str:
    w = re.sub(r"[^a-z]", "", word.lower())
    return NICKNAMES.get(w, w)


def same_person(person: dict | None, agent: dict) -> bool:
    """Both names match: first name allowing nicknames and short forms
    (Chris / Christopher), last name allowing a double-barrelled one."""
    if not person:
        return False
    first, last = split_name(agent.get("name"))
    first, last = agent.get("first_name") or first, agent.get("last_name") or last
    a_first, p_first = _first(first), _first(person.get("first") or "")
    a_lasts, p_lasts = _parts(last), _parts(person.get("last"))
    if not (a_first and p_first and a_lasts & p_lasts):
        return False
    return a_first == p_first or (min(len(a_first), len(p_first)) >= 3
                                  and (a_first.startswith(p_first) or p_first.startswith(a_first)))


def _parts(last: str | None) -> set[str]:
    return {w for w in (re.sub(r"[^a-z]", "", x) for x in re.split(r"[\s-]+", (last or "").lower())) if len(w) > 1}


def state_of(agent: dict, default: str | None) -> str | None:
    """The agent's state: from a market label ("Broward County, FL"), else the job's."""
    for text in ((agent.get("markets") or ""), (agent.get("cities") or "")):
        m = re.search(r",\s*([A-Z]{2})\b", text)
        if m:
            return m.group(1)
    return default


def home_state(numbers) -> str | None:
    """The state most of a list's numbers are from, by area code: an imported
    list rarely says which state its cities are in."""
    counts = Counter()
    for e164 in numbers:
        try:
            where = geocoder.description_for_number(phonenumbers.parse(e164), "en")
        except phonenumbers.NumberParseException:
            continue
        last = where.split(",")[-1].strip()
        state = STATES.get(last.lower()) or (last if re.fullmatch(r"[A-Z]{2}", last) else None)
        if state:
            counts[state] += 1
    return counts.most_common(1)[0][0] if counts else None


def identify(agent: dict, state: str | None, usage=None) -> dict | None:
    """The agent's own record: by their email when they have one (an email
    is one person's), then by name and city. Only a record whose names
    match, and, from a name lookup, that lives or lived in their state, is
    returned; anything else is a namesake."""
    email = agent.get("email")
    if email:
        person = run(usage, email=email)
        if same_person(person, agent):
            return person
    city = (agent.get("cities") or "").split(";")[0].strip()
    address = ", ".join(x for x in (city, state) if x)
    person = run(usage, name=agent.get("name"), address=address or None)
    if same_person(person, agent) and (not state or state in person["states"]):
        return person
    return None
