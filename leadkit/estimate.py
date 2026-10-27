"""What a job will cost and how long it will take, before anything is spent.

Counts come from the job itself wherever they are known: the listings
already enumerated, the agents already rolled up, the rows already
imported. Only what can't be known yet (how many agents 30,000 listings
collapse into, how many emails will turn up) uses rates measured on the
South Florida run, and the quote says so.
"""
from __future__ import annotations

import math
from collections import Counter

from leadkit import emailverify, phones, settings
from leadkit.jobs import Job

P = settings.PRICES

# Share of agents left after a minimum-listings filter (South Florida run:
# 19,686 agents, 8,139 with 2+, 1,529 with 5+; 3 and 4 interpolated).
MIN_LISTINGS_SHARE = {1: 1.0, 2: 0.41, 3: 0.24, 4: 0.14, 5: 0.078}

# Share of agents whose number needs fixing, before anyone has looked, and
# how many paid lookups finding a replacement takes on average.
FIX_SHARE = 0.5
AVG_FIX_LOOKUPS = 2

# Share of agents whose listings come with an email (Realtor.com, 2026-10-27:
# 30 of 32 listings had one).
LISTING_EMAIL_SHARE = 0.9
# Realtor.com listings bought per listing kept: pending ones it returns
# anyway, and bands bought twice after coming back full.
LISTING_OVERHEAD = 1.2

# Items per second at the default worker counts, rough.
RATE = {"fix_phones": 0.5, "listing_searches": 0.5, "emails": 0.8, "deep_emails": 0.075, "verify_emails": 6.0,
        "verify_phones": 2.0, "volume": 0.4}


def _search_price() -> float:
    return P["monid_call"]


SCOPE_WORDS = {"emails": "agents with a usable email", "producers": "producers"}
# Before homes.com has been checked, the share of agents who turn out to be
# producers (5+ listings sold a year). A guess; the quote says so.
PRODUCER_SHARE = 0.3


def _phone_scope_count(job: Job, store, agents: int, estimated: bool, pending: int,
                       notes: list[str]) -> tuple[int, bool]:
    """(agents the phone steps will cover, whether that count is exact)."""
    if job.phones_for == "emails":
        have = 0 if estimated else store.one(
            "SELECT COUNT(*) FROM agents WHERE email IS NOT NULL "
            "AND COALESCE(email_check, 'ok') IN ('ok', 'catch_all')") or 0
        expect = have + (pending * settings.EMAIL_HIT_RATE if "emails" in job.addons else 0)
        return int(expect), not ("emails" in job.addons and pending)
    if job.phones_for == "producers":
        unchecked = store.one("SELECT COUNT(*) FROM agents WHERE volume_status IS NULL") or 0
        if not estimated and not unchecked:
            return store.one("SELECT COUNT(*) FROM agents WHERE seller_deals_year >= ?", (job.producer_min,)) or 0, True
        notes.append(f"Phone steps assume {PRODUCER_SHARE:.0%} of agents are producers; "
                     "the real count is known after the homes.com check.")
        return int(agents * PRODUCER_SHARE), False
    return agents, not estimated


def _numbers_to_look_up(store) -> int:
    """Unchecked numbers that pass the free checks: a real format, and not
    shared with another agent."""
    from collections import Counter
    rows = store.agents("phone IS NOT NULL")
    e164 = {a["key"]: a["phone_e164"] or phones.normalize(a["phone"]) for a in rows}
    counts = Counter(n for n in e164.values() if n)
    return sum(1 for a in rows if a["phone_status"] is None and e164[a["key"]] and counts[e164[a["key"]]] < 2)


def fix_cost_each(job: Job) -> float:
    # The agent's own record by email and/or name (each billed only when it
    # finds someone), and for about half, web searches plus checks on the
    # numbers they turn up.
    trace = 1.5 * settings.PRICES["skip_trace"]
    web = 2 * _search_price() + AVG_FIX_LOOKUPS * phones.lookup_price()
    return trace + 0.5 * web


def fix_summary(job: Job) -> dict:
    """After the phone check: how many numbers look wrong, why, and what
    finding the real ones would cost. Priced on the real count."""
    store = job.store()
    bad, _ = phones.to_fix(job, store)
    checked = store.one("SELECT COUNT(*) FROM agents WHERE phone_status IS NOT NULL") or 0
    store.close()
    kinds = Counter(phones.kind_of_problem(a["reason"]) for a in bad)
    return {"bad": len(bad), "checked": checked, "kinds": dict(kinds.most_common()),
            "cost": round(len(bad) * fix_cost_each(job), 2)}


def format_fix_summary(job: Job, s: dict) -> str:
    if not s["checked"]:
        return "No numbers have been checked yet."
    if not s["bad"]:
        return f"All {s['checked']:,} checked numbers look like the agents' own cells."
    why = ", ".join(f"{k}: {n:,}" for k, n in s["kinds"].items())
    return (f"{s['bad']:,} of {s['checked']:,} numbers look wrong ({why}).\n"
            f"Finding their real cells would cost about ${s['cost']:,.2f} "
            f"(about ${fix_cost_each(job):.3f} each; you only pay for the search, found or not).\n"
            f"To do it: python leads.py add {job.name} fix_phones")


def spend(usage: dict) -> dict[str, float]:
    """Dollars actually spent so far, by provider."""
    out = {}
    for provider, u in usage.items():
        if provider == "monid":
            out[provider] = u["calls"] * P["monid_call"]
        elif provider in ("phone", "email", "skiptrace", "zillow", "realtor"):
            out[provider] = u["units"]  # recorded in dollars
    return out


def quote(job: Job) -> dict:
    store = job.store()
    lines: list[dict] = []
    notes: list[str] = []
    estimated = False

    def add(step, what, count, unit_cost, rate_key=None):
        count = max(0, int(count))
        hours = count / RATE[rate_key] / 3600 if rate_key else 0
        lines.append({"step": step, "what": what, "count": count,
                      "cost": round(count * unit_cost, 2), "hours": round(hours, 1)})

    if job.mode == "build":
        bands, expected = store.q("SELECT COUNT(*), COALESCE(SUM(expected), 0) FROM bands WHERE status IS NULL")[0]
        found = store.one("SELECT COUNT(*) FROM listings") or 0
        listings = found + expected
        per_band = expected / bands if bands else 0
        add("listings", "searches on Realtor.com, each up to 25 listings with the agent's name, phone and email",
            bands, P["realtor_call"] + per_band * LISTING_OVERHEAD * P["realtor_listing"], "listing_searches")
        agents_known = store.one("SELECT COUNT(*) FROM agents") or 0
        if agents_known and not bands:
            agents = agents_known
        else:
            estimated = True
            share = MIN_LISTINGS_SHARE.get(job.min_listings, 0.05)
            agents = math.ceil(listings / settings.LISTINGS_PER_AGENT * share)
    else:
        listings = 0
        agents = store.one("SELECT COUNT(*) FROM agents") or 0

    def known(sql, fallback):
        return store.one(sql) if not estimated else fallback

    # Emails the first pass will find, and the misses a deep pass retries. On
    # a new list most agents' emails come with their listings.
    pending = known("SELECT COUNT(*) FROM agents WHERE email IS NULL AND email_status IS NULL",
                    agents * (1 - LISTING_EMAIL_SHARE) if job.mode == "build" else agents)
    if "emails" in job.addons:
        add("emails", "agents searched for an email (2 searches each)", pending, 2 * _search_price(), "emails")
    if "deep_emails" in job.addons:
        misses = known("SELECT COUNT(*) FROM agents WHERE email_status = 'not_found'", 0)
        if "emails" in job.addons:
            misses += pending * 0.6
        add("deep_emails", "agents retried in Chrome (2 searches each, pages free)", misses,
            2 * _search_price(), "deep_emails")
    if "verify_emails" in job.addons:
        have = known("SELECT COUNT(*) FROM agents WHERE email IS NOT NULL AND email_check IS NULL",
                     agents * LISTING_EMAIL_SHARE if job.mode == "build" else 0)
        if "emails" in job.addons:
            have += pending * settings.EMAIL_HIT_RATE
        add("verify_emails", "emails verified (only the ones found)", have, emailverify.price(), "verify_emails")
    if "volume" in job.addons:
        todo = known("SELECT COUNT(*) FROM agents WHERE volume_status IS NULL", agents)
        add("volume", "agents looked up on homes.com (~1.5 searches + 1 page each)", todo,
            1.5 * _search_price() + 1.1 * P["monid_call"], "volume")

    if "verify_phones" in job.addons or "fix_phones" in job.addons:
        in_scope, exact = _phone_scope_count(job, store, agents, estimated, pending, notes)
        price = phones.lookup_price()
        what = "phone numbers checked: whose number it is and if it's a cell (free when nobody is found)"
        if job.phones_for != "all":
            what += f" (only {SCOPE_WORDS[job.phones_for]})"
        if "verify_phones" in job.addons:
            count = _numbers_to_look_up(store) if exact and job.phones_for == "all" else in_scope
            add("verify_phones", what, count, price, "verify_phones")
        if "fix_phones" in job.addons:
            unchecked = store.one("SELECT COUNT(*) FROM agents WHERE phone_status IS NULL AND phone IS NOT NULL") or 0
            if not estimated and not unchecked:
                fix = len(phones.to_fix(job, store)[0])
            else:
                # Not looked up yet: on listing data roughly half the numbers are
                # an office line or a landline. A guess, so the quote says so.
                notes.append("fix_phones assumes half the numbers need fixing; the real count is known "
                             "once phones are checked.")
                fix = in_scope * FIX_SHARE
            add("fix_phones", "agents whose number isn't their own cell: looked up by email or name, "
                "then searched and up to 3 candidates checked",
                fix, fix_cost_each(job), "fix_phones")

    total = round(sum(l["cost"] for l in lines), 2)
    hours = round(sum(l["hours"] for l in lines), 1)
    result = {"listings": listings, "agents": agents, "agents_estimated": estimated,
              "lines": lines, "total": total, "hours": hours, "notes": notes,
              "spent_so_far": round(sum(spend(store.usage()).values()), 2)}
    store.close()
    return result


def missing_keys(job: Job) -> dict[str, str]:
    """{step: what it needs} for every step this job would stop at."""
    if settings.MONID_API_KEY:
        return {}
    return {step: "MONID_API_KEY" for step in job.steps()}


def services_needed(job: Job) -> set[str]:
    """Which services this job needs keys for, as named on the keys page."""
    return {"monid"} if job.steps() else set()


def format_quote(q: dict) -> str:
    out = []
    if q["listings"]:
        out.append(f"Active listings found: {q['listings']:,}")
    out.append(f"Agents: {'about ' if q['agents_estimated'] else ''}{q['agents']:,}"
               + (" (estimated from listings; exact once the listings are in)" if q["agents_estimated"] else ""))
    out.append("")
    out.append(f"  {'step':<15}{'count':>9}  {'cost':>9}  {'time':>7}  what")
    for l in q["lines"]:
        out.append(f"  {l['step']:<15}{l['count']:>9,}  ${l['cost']:>8,.2f}  {l['hours']:>5.1f} h  {l['what']}")
    out.append(f"  {'TOTAL':<15}{'':>9}  ${q['total']:>8,.2f}  {q['hours']:>5.1f} h")
    for note in q.get("notes", []):
        out.append(f"Note: {note}")
    if q["spent_so_far"]:
        out.append(f"\nAlready spent on this job: ${q['spent_so_far']:,.2f}")
    out.append("\nPrices are Monid's list prices (PRICE_* in .env to change them); times are rough and run unattended.")
    return "\n".join(out)
