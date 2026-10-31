"""Finding an agent's own cell when the number on file isn't it.

First the agent's own skip trace record (with Monid): the one the phone
check already fetched when the number on file was theirs, free, or else a
trace by their email or name, checked to be them and not a namesake. A
Wireless number on that record is their cell.

Then their Zillow profile, whose "cell" is the number they gave Zillow
themselves. A profile is found through search and only counts if it's
provably them: the same email, or both names in their state plus something
local (the number on file, their brokerage, or their city on the profile).
Name and state alone aren't enough: on the Weston test that let in a Maria
Lopez from Palm Beach County for the Maria Lopez in Weston.

Without one, candidates come from everywhere the agent has published a
number, cheapest first: the other numbers on their own listings, their
homes.com profile (read for free during the volume check), the page their
email was found on, then two web searches and the best few result pages.
On a page, a number labelled "cell", "mobile", "direct" or "text", or
printed close to the agent's name, ranks above the rest.

Numbers that belong to anyone else in the job (an office line on several
agents, a teammate's cell) and toll-free numbers are dropped for free.
Then candidates are traced, best first, and the first one that is a cell
and isn't on someone else's record wins. A trace costs money, so at most
MAX_LOOKUPS are tried per agent.

Last, a people database (DataLegion), the priciest and the weakest. Its
matches on name and state are often someone else entirely (on that test, a
cruise line manager and a bank vice president), so one only counts when it
matched their email, or their name plus their brokerage or city.
"""
from __future__ import annotations

import html as htmllib
import json
import re
from collections import defaultdict
from urllib.parse import unquote

import phonenumbers

from leadkit import monid, phones, settings, skiptrace
from leadkit.emails.finder import rank_urls, corporate_domain
from leadkit.names import split_name
from leadkit.search import search

MAX_LOOKUPS = 3
MAX_PAGES = 3
MAX_PROFILES = 3
_PROFILE = re.compile(r"zillow\.com/profile/([^/?#]+)", re.I)
# Words every brokerage shares, which say nothing about which one it is.
_FIRM_WORDS = set("llc inc corp pa the of and real estate realty realtors group properties property brokers "
                  "brokerage associates international company homes fl florida re".split())
_LABEL = re.compile(r"(cell|mobile|direct|text|\bc\s*[:.)]|\bm\s*[:.)])", re.I)
_OFFICE = re.compile(r"(office|main|fax|toll|brokerage|front desk|\bo\s*[:.)]|\bf\s*[:.)])", re.I)
_TEL = re.compile(r"tel:\s*([+\d().\s-]{7,25})", re.I)


def page_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]{0,200}>.{0,200000}?</\1>", " ", (html or "")[:800_000])
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]{0,2000}>", " ", html)))


def numbers_on_page(html: str, first: str, last: str) -> dict[str, int]:
    """{e164: score} for every US number on a page."""
    text = page_text(html)
    low = text.lower()
    name_spots = [m.start() for m in re.finditer(re.escape(last.lower()), low)] if len(last) > 1 else []
    found: dict[str, int] = defaultdict(int)
    for m in phonenumbers.PhoneNumberMatcher(text, "US"):
        e164 = phonenumbers.format_number(m.number, phonenumbers.PhoneNumberFormat.E164)
        before = text[max(0, m.start - 40):m.start]
        score = 1
        if _LABEL.search(before):
            score += 3
        if _OFFICE.search(before):
            score -= 3
        if any(abs(spot - m.start) < 300 for spot in name_spots):
            score += 2
        found[e164] = max(found[e164], score)
    for raw in _TEL.findall(html or ""):
        e164 = phones.normalize(raw)
        if e164:
            found[e164] = max(found[e164], 1) + 1
    return dict(found)


def _toll_free(e164: str) -> bool:
    return phonenumbers.number_type(phonenumbers.parse(e164)) == phonenumbers.PhoneNumberType.TOLL_FREE


def gather(agent: dict, fetch_session, usage=None) -> list[tuple[str, int, str]]:
    """[(e164, score, where)] best first, before any exclusions."""
    first, last = agent.get("first_name") or "", agent.get("last_name") or ""
    scores: dict[str, int] = defaultdict(int)
    where: dict[str, str] = {}

    def add(e164, score, source):
        if not e164:
            return
        scores[e164] += score
        where.setdefault(e164, source)

    for raw in (agent.get("other_phones") or "").split(";"):
        add(phones.normalize(raw.strip()), 3, "another of their listings")
    if agent.get("homes_phone"):
        add(phones.normalize(agent["homes_phone"]), 3, agent.get("homes_profile") or "homes.com")

    pages = [agent["email_source"]] if (agent.get("email_source") or "").startswith("http") else []
    city = (agent.get("cities") or "").split(";")[0].strip()
    name = agent.get("name") or f"{first} {last}"

    urls: list[str] = []
    for q in (f'"{name}" realtor {city} cell'.replace("  ", " "),
              f'"{name}" {agent.get("brokerage") or ""} phone'.replace("  ", " ")):
        urls += [u for u in search(q, usage=usage) if u not in urls]
    pages += [u for u in rank_urls(urls, corporate_domain(agent.get("brokerage")), name) if u not in pages]

    for url in pages[:MAX_PAGES + 1]:
        try:
            html = fetch_session.get(url).text
        except Exception:
            continue
        for e164, score in numbers_on_page(html, first, last).items():
            add(e164, score, url)
    return sorted(((e, s, where[e]) for e, s in scores.items()), key=lambda c: -c[1])


def own_record(agent: dict, state: str | None, usage=None) -> dict | None:
    """The agent's skip trace record: the phone check's, when it was theirs,
    or a new trace by email or name."""
    person = json.loads(agent["phone_trace"]) if agent.get("phone_trace") else None
    if skiptrace.same_person(person, agent):
        return person
    if not settings.MONID_API_KEY:
        return None
    return skiptrace.identify(agent, state, usage)


def _same_firm(a: str | None, b: str | None) -> bool:
    words = [set(re.findall(r"[a-z0-9]+", (x or "").lower())) - _FIRM_WORDS for x in (a, b)]
    return bool(words[0] & words[1])


def _middle_names_differ(a: str | None, b: str | None) -> bool:
    """Maria Contardo Rodriguez isn't Maria Eugenia Rodriguez."""
    middles = [{w for w in re.findall(r"[a-z]+", (x or "").lower())[1:-1] if len(w) > 1} for x in (a, b)]
    return bool(middles[0] and middles[1] and not middles[0] & middles[1])


def _is_their_profile(data: dict, agent: dict, state: str | None, cell: str) -> bool:
    user, email = data.get("user") or {}, (agent.get("email") or "").lower()
    if email and email == (user.get("email") or "").lower():
        return True
    first, last = split_name(user.get("name"))
    states = {(user.get("businessAddress") or {}).get("state")} | {l.get("state") for l in data.get("licenses") or []}
    if not skiptrace.same_person({"first": first, "last": last}, agent) or (state and state not in states):
        return False
    if cell == agent.get("phone_e164"):
        return True
    if _middle_names_differ(agent.get("name"), user.get("name")):
        return False
    text = json.dumps(data).lower()
    cities = [c.strip().lower() for c in (agent.get("cities") or "").split(";") if c.strip()]
    return _same_firm(agent.get("brokerage"), user.get("businessName")) or any(c in text for c in cities)


def _profile(slug: str, usage=None) -> dict | None:
    try:
        res = monid.run("zillow", "/get_agent_profile", query={"username": slug}, attempts=1)
    except monid.MonidError:
        raise
    except Exception:
        return None
    if usage:
        usage("zillow", 1, (res.get("cost") or {}).get("value") or 0)
    if (res.get("providerResponse") or {}).get("httpStatus", 200) >= 400:
        return None
    return (res.get("output") or {}).get("data")


def zillow_cell(agent: dict, state: str | None, usage=None) -> dict | None:
    """The cell on the agent's own Zillow profile, or None."""
    name, tried = agent.get("name") or "", set()
    city = (agent.get("cities") or "").split(";")[0].strip()
    for q in (f"{name} real estate agent {city} zillow profile reviews",
              f"{name} {agent.get('brokerage') or ''} zillow.com/profile"):
        for url in search(q.replace("  ", " "), usage=usage):
            m = _PROFILE.search(url)
            slug = unquote(m.group(1)) if m else None
            if not slug or slug in tried or len(tried) >= MAX_PROFILES:
                continue
            tried.add(slug)
            data = _profile(slug, usage) or {}
            user = data.get("user") or {}
            e164 = phones.normalize((user.get("phoneNumbers") or {}).get("cell"))
            if e164 and _is_their_profile(data, agent, state, e164):
                return {"valid": True, "type": "mobile", "carrier": None, "caller_name": user.get("name"),
                        "caller_type": None, "name_match": "match", "e164": e164,
                        "source": f"https://www.zillow.com/profile/{m.group(1)}"}
    return None


def people_cell(agent: dict, state: str | None, usage=None) -> dict | None:
    """The mobile a people database has for the agent, or None."""
    wanted = {"email": agent.get("email"), "full_name": agent.get("name"), "state": state,
              "city": (agent.get("cities") or "").split(";")[0].strip()}
    try:
        res = monid.run("datalegion", "/person/enrich/base/pii", {k: v for k, v in wanted.items() if v}, attempts=1)
    except monid.MonidError:
        raise
    except Exception:
        return None
    if (res.get("providerResponse") or {}).get("httpStatus", 200) >= 400:
        return None
    if usage:
        cost = (res.get("cost") or {}).get("value")
        usage("skiptrace", 1, settings.PRICES["people_lookup"] if cost is None else cost)
    matches = (res.get("output") or {}).get("matches") or []
    if not matches:
        return None
    person, how = matches[0].get("person") or {}, (matches[0].get("match_metadata") or {}).get("matched_on") or []
    named = skiptrace.same_person({"first": person.get("first_name"), "last": person.get("last_name")}, agent)
    here = not state or str(person.get("state_code") or "").endswith(f"-{state}")
    city = str(person.get("city") or "").lower()
    local = _same_firm(agent.get("brokerage"), person.get("company_name")) or (
        city and city in (agent.get("cities") or "").lower())
    if not ("email" in how or (named and ("email_domain" in how or (here and local)))):
        return None
    e164 = phones.normalize(person.get("mobile_phone"))
    return e164 and {"valid": True, "type": "mobile", "carrier": None, "caller_name": person.get("full_name"),
                     "caller_type": None, "name_match": "match", "e164": e164, "source": "people database"}


def _usable(cell: dict | None, agent: dict, taken: set[str]) -> dict | None:
    """A cell from a source that's sure it's them, once it's checked against
    the job: the number on file comes back "confirmed" (unless several agents
    share it, which makes it an office line), anyone else's is dropped."""
    if not cell or _toll_free(cell["e164"]):
        return None
    if cell["e164"] == agent.get("phone_e164") and not (agent.get("reason") or "").startswith("shared"):
        return {**cell, "confirmed": True}
    return None if cell["e164"] in taken else cell


def find_cell(agent: dict, taken: set[str], fetch_session, usage=None, state: str | None = None) -> dict | None:
    """The agent's own cell, verified, or None. `taken` is every number on
    any agent in the job; none of those can be this agent's own cell, except
    the one on file when their record says it's their cell ("confirmed")."""
    person = own_record(agent, state, usage)
    if person:
        found = {"valid": True, "type": "mobile", "carrier": None, "caller_name": person["name"],
                 "caller_type": None, "name_match": "match", "source": "skip trace"}
        if dict(map(tuple, person["phones"])).get(agent.get("phone_e164")) == "mobile":
            return {**found, "e164": agent["phone_e164"], "confirmed": True}
        for e164 in skiptrace.cells(person, taken):
            if not _toll_free(e164):
                return {**found, "e164": e164}
    cell = _usable(zillow_cell(agent, state, usage), agent, taken)
    if cell:
        return cell
    tried = 0
    for e164, score, source in gather(agent, fetch_session, usage):
        if e164 in taken or _toll_free(e164) or score <= 0:
            continue
        if tried >= MAX_LOOKUPS:
            break
        tried += 1
        r = phones.lookup(e164, usage=usage)
        match = phones.name_match(r["caller_name"], agent.get("first_name") or "", agent.get("last_name") or "")
        if r["valid"] and r["type"] == "mobile" and match != "mismatch":
            return {**r, "e164": e164, "name_match": match, "source": source}
    return _usable(people_cell(agent, state, usage), agent, taken)
