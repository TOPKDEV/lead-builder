"""The files people actually use, written to exports/<job>/.

  everyone.csv        every agent and everything known about them
  email-ready.csv     verified deliverable emails only (or every found email,
                      flagged, when verification wasn't run)
  email-probably-ok.csv  emails no check can confirm, that came from the agent's
                      own listing or the person's own list, not from a search:
                      catch-all domains (which accept any address), and mail
                      servers that won't say (Yahoo, AOL, many brokerages)
  cell-phones.csv     numbers confirmed as mobile, not a known TCPA litigator, and not registered
                      to someone else (including cells found by fix_phones)
  producers.csv       sold at least N listings last year, listing mills removed

Columns use plain names so the files upload straight into Instantly,
Smartlead, a CRM or a dialer. An imported list keeps its own columns too.
"""
from __future__ import annotations

import csv
import json
from datetime import date

from leadkit import phones, volume
from leadkit.jobs import Job

COLUMNS = [
    ("first_name", "first_name"), ("last_name", "last_name"), ("name", "full_name"),
    ("email", "email"), ("email_check", "email_verified"), ("email_source", "email_found_on"),
    ("phone_pretty", "phone"), ("phone_type", "phone_type"), ("phone_line", "carrier_says"),
    ("phone_carrier", "phone_carrier"),
    ("phone_caller_name", "phone_registered_to"), ("phone_name_match", "phone_matches_agent"),
    ("phone_found_on", "phone_found_on"), ("phone_original", "office_or_old_phone"),
    ("phone_note", "phone_note"), ("phone_litigator_flag", "known_tcpa_litigator"), ("other_phones", "other_phones_on_listings"),
    ("brokerage", "brokerage"), ("cities", "cities"), ("markets", "markets"),
    ("active_listings", "active_listings"), ("total_volume", "active_listing_volume"),
    ("median_price", "median_list_price"), ("avg_dom", "avg_days_on_market"),
    ("seller_deals_year", "listings_sold_last_12mo"), ("deals_year", "total_deals_last_12mo"),
    ("zillow_sales_year", "zillow_sales_last_12mo"), ("zillow_sales_total", "zillow_total_sales"),
    ("seller_volume_year", "sold_volume_last_12mo"), ("seller_avg_price", "avg_sold_price"),
    ("closed_sales_5yr", "closed_sales_5yr"), ("listing_mill", "flat_fee_or_mill"),
    ("homes_profile", "homes_com_profile"), ("zillow_profile", "zillow_profile"), ("sample_urls", "sample_listings"),
    ("rows_merged", "duplicate_rows_merged"),
]


def _row(agent: dict, mapped: set[str]) -> dict:
    a = dict(agent)
    if a.get("phone_e164"):
        a["phone_pretty"] = phones.pretty(a["phone_e164"])
    else:
        a["phone_pretty"] = a.get("phone") or ""
    if a.get("phone_status") == "invalid_format":
        a["phone_type"] = "invalid number"
    elif a.get("phone_status") == "shared":
        a["phone_type"] = "office line (shared by several agents)"
    if a.get("phone_fix") == "replaced":
        a["phone_note"] = f"replaced: the old number was {a['phone_note']}"
    elif a.get("phone_fix") == "confirmed":
        a["phone_note"] = f"confirmed as their cell by their own record (it looked like: {a['phone_note']})"
    elif a.get("phone_fix") == "not_found":
        a["phone_note"] = f"{a['phone_note']}; no better number found"
        if a.get("phone_line") == "mobile":
            a["phone_note"] += "; a carrier says it's a cell, but nothing shows it's theirs"
    a["phone_litigator_flag"] = {1: "YES, don't text or auto-dial", 0: "no"}.get(a.get("phone_litigator"), "")
    a["phone_name_match"] = {"match": "yes", "mismatch": "NO"}.get(a.get("phone_name_match") or "", "")
    if a.get("volume_status") == "found":
        a["listing_mill"] = "yes" if volume.is_listing_mill(a["seller_deals_year"], a["brokerage"]) else "no"
    if a.get("email_status") in ("not_found", "exhausted") and not a.get("email"):
        a["email_check"] = "no email found"
    for k in ("total_volume", "median_price", "seller_volume_year", "seller_avg_price"):
        if a.get(k) is not None:
            a[k] = int(a[k])
    out = {label: ("" if a.get(src) is None else a.get(src)) for src, label in COLUMNS}
    if a.get("volume_status") in ("no_profile", "unreadable"):
        out["listings_sold_last_12mo"] = "no homes.com profile" if a["volume_status"] == "no_profile" else "unreadable"
    extra = json.loads(a["extra"]) if a.get("extra") else {}
    for k, v in extra.items():
        if k in mapped:  # already out under its standard name
            continue
        out.setdefault(k if k not in out else f"original_{k}", v)
    return out


# Where the checker's "the mail server wouldn't say" really means the
# mailbox doesn't exist: made-up Gmail addresses get that answer, real ones
# don't. On Yahoo, AOL and most brokerage servers it just can't tell
# (Weston, FL, 2026-10-27: 34 of 36 such answers were on those).
TRUSTED_VERIFIER_DOMAINS = {"gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com", "msn.com"}


def _unconfirmable(agent: dict) -> bool:
    check = agent.get("email_check")
    domain = (agent.get("email") or "").rsplit("@", 1)[-1].lower()
    return check == "catch_all" or (check == "unknown" and domain not in TRUSTED_VERIFIER_DOMAINS)


def _write(path, rows: list[dict]) -> int:
    header: list[str] = []
    for r in rows:
        header += [k for k in r if k not in header]
    # Drop columns that are empty for everyone: a build has no imported
    # columns, an enrich job without volume has no production columns.
    header = [h for h in header if any(str(r.get(h, "")) != "" for r in rows)] or [l for _, l in COLUMNS]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def export(job: Job) -> dict[str, tuple[str, int]]:
    store = job.store()
    agents = store.agents()
    store.close()
    job.export_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    mapped = set(job.column_map.values())
    files = {}

    def write(kind: str, selected: list[dict]):
        path = job.export_dir / f"{job.name}-{kind}-{stamp}.csv"
        files[kind] = (str(path), _write(path, [_row(a, mapped) for a in selected]))

    write("everyone", agents)
    with_email = [a for a in agents if a.get("email")]
    verified = any(a.get("email_check") for a in with_email)
    if verified:
        write("email-ready", [a for a in with_email if a.get("email_check") == "ok"])
        probably = [a for a in with_email if _unconfirmable(a)
                    and (a.get("email_status") == "imported" or a.get("email_source") == "realtor.com listing")]
        if probably:
            write("email-probably-ok", probably)
    elif with_email:
        write("email-ready-UNVERIFIED", with_email)
    # Only one email file at a time: an unverified one from before the check
    # ran, left next to the verified one, would get sent by mistake.
    stale = "email-ready-UNVERIFIED" if verified else "email-ready"
    for old in job.export_dir.glob(f"{job.name}-{stale}-*.csv"):
        if stale == "email-ready-UNVERIFIED" or "UNVERIFIED" not in old.name:
            old.unlink()
    if any(a.get("phone_status") == "checked" for a in agents):
        # A cell registered to someone else is not this agent's cell.
        write("cell-phones", [a for a in agents if a.get("phone_type") == "mobile" and not a.get("phone_litigator")
                              and a.get("phone_name_match") != "mismatch"])
        probably = [a for a in agents if a.get("phone_line") == "mobile" and a.get("phone_type") == "unknown"]
        if probably:
            write("cell-phones-probably", probably)
    if any(a.get("volume_status") == "found" or a.get("zillow_sales_year") is not None for a in agents):
        write("producers", [a for a in agents if volume.is_producer(a, job.producer_min)])
    return files
