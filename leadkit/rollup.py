"""Listings to unique agents.

One agent appears on many listings, spelled slightly
differently from one listing to the next ("Chris Maners PA", "Christopher
Maners"). Agents are keyed on first + last name with suffixes dropped and
nicknames collapsed (see names.py). Brokerage is deliberately not part of
the key: it is blank on many listings, and keying on it would split one
agent into several records.

Phones and emails are not used to merge either: a phone on a listing is
often the brokerage's front desk, shared by dozens of agents. The phone kept for an
agent is the one that appears on most of their listings.
"""
from __future__ import annotations

from collections import Counter

from leadkit.names import name_key, split_name
from leadkit.store import Store


def build_agents(store: Store, min_listings: int = 1) -> dict:
    groups: dict[str, list] = {}
    unattributed = 0
    for row in store.q("SELECT * FROM listings"):
        key = name_key(row["agent_name"])
        if not key:
            unattributed += 1
            continue
        groups.setdefault(key, []).append(row)

    rows = []
    for key, listings in groups.items():
        if len(listings) < min_listings:
            continue
        names = Counter(" ".join(l["agent_name"].split()) for l in listings)
        name = names.most_common(1)[0][0]
        phones = Counter(l["agent_phone"] for l in listings if l["agent_phone"])
        brokerages = Counter(l["agent_brokerage"] for l in listings if l["agent_brokerage"])
        emails = Counter(l["agent_email"] for l in listings if l["agent_email"])
        prices = sorted(l["price"] for l in listings if l["price"])
        doms = [l["days_on_market"] for l in listings if l["days_on_market"] is not None]
        first, last = split_name(name)
        ranked_phones = [p for p, _ in phones.most_common()]
        rows.append((
            key, name, first, last,
            ranked_phones[0] if ranked_phones else None,
            "; ".join(ranked_phones[1:]),
            brokerages.most_common(1)[0][0] if brokerages else None,
            "; ".join(sorted({l["city"] for l in listings if l["city"]})),
            "; ".join(sorted({l["market"] for l in listings if l["market"]})),
            len(listings),
            sum(prices),
            prices[len(prices) // 2] if prices else None,
            round(sum(doms) / len(doms), 1) if doms else None,
            "; ".join(l["listing_url"] for l in listings[:3] if l["listing_url"]),
            emails.most_common(1)[0][0] if emails else None,
        ))

    # Refresh the listing stats, never the enrichment already paid for.
    store.write_many("""
        INSERT INTO agents (key, name, first_name, last_name, phone, other_phones, brokerage,
            cities, markets, active_listings, total_volume, median_price, avg_dom, sample_urls,
            email)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET
            name=excluded.name, phone=COALESCE(excluded.phone, phone),
            other_phones=excluded.other_phones, brokerage=excluded.brokerage,
            cities=excluded.cities, markets=excluded.markets,
            active_listings=excluded.active_listings, total_volume=excluded.total_volume,
            median_price=excluded.median_price, avg_dom=excluded.avg_dom,
            sample_urls=excluded.sample_urls, email=COALESCE(email, excluded.email)
    """, rows)
    # The email that came with their listings counts as found: the email
    # search skips them, and the verifier still checks it.
    store.write("""UPDATE agents SET email_status = 'found', email_source = 'realtor.com listing'
                   WHERE email IS NOT NULL AND email_status IS NULL""")
    total_listings = sum(len(v) for v in groups.values())
    return {"listings": total_listings + unattributed, "unattributed": unattributed,
            "agents": len(rows), "below_min": len(groups) - len(rows)}

