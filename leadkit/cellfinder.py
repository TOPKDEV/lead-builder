"""Finding an agent's own cell when the number on file isn't it.

First the agent's own skip trace record (with Monid): the one the phone
check already fetched when the number on file was theirs, free, or else a
trace by their email or name, checked to be them and not a namesake. A
Wireless number on that record is their cell.

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
"""
from __future__ import annotations

import html as htmllib
import json
import re
from collections import defaultdict

import phonenumbers

from leadkit import phones, settings, skiptrace
from leadkit.emails.finder import rank_urls, corporate_domain
from leadkit.search import search

MAX_LOOKUPS = 3
MAX_PAGES = 3
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
    return None
