"""Agent production from homes.com: how many listings they closed last year.

No public API reports agent production, and homes.com blocks scrapers on
its search pages. What works: a search engine hands over the agent's
homes.com profile URL, and the profile renders through Firecrawl's enhanced
proxy (via Monid). Two calls, about a fifth of a cent an agent.

Seller deals in the last twelve months is the number that matters. A busy
buyer's agent has nothing to list, and five a year is roughly a listing
every ten weeks: a working listing agent rather than a hobbyist.

The profile's Transaction History has one, two and five year tabs, and in
each the heading sits BELOW the table it names: a table, "Seller Deals",
another table, "Buyer Deals". The check that this reading is right is that
seller plus buyer over five years equals the headline "Closed Sales"
figure. A page that does not add up is flagged rather than believed.
"""
from __future__ import annotations

import re

from leadkit import monid
from leadkit.names import search_name
from leadkit.search import search

PRODUCER_MIN_SELLER_DEALS = 5
# Flat-fee MLS "agents" list thousands of homes a year for sellers who do
# everything themselves. They pass any production bar and never hire anyone.
LISTING_MILL_MIN_DEALS = 250
FLAT_FEE = re.compile(r"(flat\s*fee|listwithfreedom|beycome|houzeo|unreal estate|homecoin|"
                      r"congress realty|entry only|spot real estate|by owner|fsbo)", re.I)

_PROFILE = re.compile(r"https://www\.homes\.com/real-estate-agents/[a-z0-9-]+/[a-z0-9]+/")


def dollars(s: str | None) -> int | None:
    """"$6.8M", "$390K" and "$1,250,000" are all dollar amounts."""
    m = re.search(r"\$?\s*([\d.,]+)\s*([KMB])?", (s or "").strip(), re.I)
    if not m:
        return None
    try:
        base = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return round(base * {"k": 1e3, "m": 1e6, "b": 1e9}.get((m.group(2) or "").lower(), 1))


def _int(s: str | None) -> int | None:
    try:
        return int(s.replace(",", "")) if s else None
    except ValueError:
        return None


def _table(block: str) -> dict:
    rng = re.search(r"\|\s*Price Range\s*\|\s*([^|]+?)\s*-\s*([^|]+?)\s*\|", block)
    deals = re.search(r"\|\s*Total Deals\s*\|\s*([\d,]+)\s*\|", block)
    value = re.search(r"\|\s*Total Value\s*\|\s*([^|]+)\|", block)
    avg = re.search(r"\|\s*Average Sale Price\s*\|\s*([^|]+)\|", block)
    return {"deals": _int(deals.group(1)) if deals else None,
            "value": dollars(value.group(1)) if value else None,
            "avg": dollars(avg.group(1)) if avg else None,
            "min": dollars(rng.group(1)) if rng else None,
            "max": dollars(rng.group(2)) if rng else None}


def _tabs(block: str) -> list[dict]:
    """Each period's {seller, buyer} tables, in order. Within a period the
    seller table comes first, so a repeated heading, or a seller table after
    a buyer one, means the next period has started."""
    tabs, current, start = [], {"seller": None, "buyer": None}, 0
    for m in re.finditer(r"###\s*(Seller Deals|Buyer Deals)", block):
        side = "seller" if m.group(1) == "Seller Deals" else "buyer"
        if current[side] or (side == "seller" and current["buyer"]):
            tabs.append(current)
            current = {"seller": None, "buyer": None}
        table = _table(block[start:m.start()])
        if table["deals"] is not None:
            current[side] = table
        start = m.end()
    if current["seller"] or current["buyer"]:
        tabs.append(current)
    return tabs


def parse_profile(markdown: str) -> dict:
    about = re.search(r"##\s*About\s+([^\n]+)", markdown)
    closed = re.search(r"([\d,]+)\s*Closed Sales", markdown)
    brokerage = re.search(r"!\[[^\]]*Real Estate Agent\s*-\s*([^\]]+?)\]", markdown)
    tel = re.search(r"\]\(tel:([^\"\n]{7,25}?)\s*\"", markdown)
    out = {"name": about.group(1).strip() if about else None,
           "brokerage": brokerage.group(1).strip() if brokerage else None,
           "closed_sales_5yr": _int(closed.group(1)) if closed else None,
           "seller_deals_year": None, "deals_year": None, "seller_volume_year": None,
           "seller_avg_price": None, "reconciled": None,
           # The number printed under the agent's name: sometimes their cell,
           # sometimes the brokerage's call centre. fix_phones decides which.
           "homes_phone": tel.group(1).strip() if tel else None}
    history = markdown.find("Transaction History")
    tabs = _tabs(markdown[history:]) if history >= 0 else []
    if not tabs:
        return out
    year, last = tabs[0], tabs[-1]
    seller = (year["seller"] or {}).get("deals") or 0
    buyer = (year["buyer"] or {}).get("deals") or 0
    five_year = ((last["seller"] or {}).get("deals") or 0) + ((last["buyer"] or {}).get("deals") or 0)
    out.update({
        "seller_deals_year": seller,
        "deals_year": seller + buyer,
        "seller_volume_year": (year["seller"] or {}).get("value"),
        "seller_avg_price": (year["seller"] or {}).get("avg"),
        "reconciled": None if out["closed_sales_5yr"] is None else five_year == out["closed_sales_5yr"],
    })
    return out


def is_listing_mill(seller_deals_year: int | None, brokerage: str | None) -> bool:
    return (seller_deals_year or 0) >= LISTING_MILL_MIN_DEALS or bool(FLAT_FEE.search(brokerage or ""))


def find_profiles(name: str, city: str | None, usage=None) -> list[str]:
    """Every homes.com profile for this person. Agents often hold two, one
    per brokerage or MLS feed, with their deals split between them."""
    clean = search_name(name) or name
    slug = "-".join(re.sub(r"[^a-z\s]", "", clean.lower()).split())
    found: list[str] = []
    for query in (f'site:homes.com "{clean}" real estate agent {city or ""}'.strip(),
                  f'homes.com "{clean}" real estate agent {city or ""}'.strip()):
        for url in search(query, usage=usage):
            m = _PROFILE.match(url)
            # The directory is full of other agents; the wrong one is worse than none.
            if m and f"/{slug}/" in m.group(0) and m.group(0) not in found:
                found.append(m.group(0))
        if found:
            break
    return found[:3]


def check(name: str, city: str | None, usage=None) -> dict:
    """{status, profile, ...production}. status is found, no_profile or unreadable."""
    profiles = find_profiles(name, city, usage=usage)
    if not profiles:
        return {"status": "no_profile"}
    readings = []
    for url in profiles:
        if usage:
            usage("monid")
        markdown = monid.render(url)
        if markdown and len(markdown) > 500:
            readings.append({**parse_profile(markdown), "profile": url})
    usable = [r for r in readings if r["seller_deals_year"] is not None and r["reconciled"] is not False]
    pool = usable or [r for r in readings if r["seller_deals_year"] is not None]
    if not pool:
        return {"status": "unreadable", "profile": profiles[0]}
    # Duplicate profiles split one person's record; the fullest one wins.
    best = max(pool, key=lambda r: r["seller_deals_year"] or 0)
    return {"status": "found", **best}
