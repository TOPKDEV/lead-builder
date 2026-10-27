"""Bring-your-own-list: read a CSV or Excel file, work out which column is
which, and load it as agents, merging duplicate rows on the way in."""
from __future__ import annotations

import csv
import re
from pathlib import Path

from leadkit.names import name_key, split_name
from leadkit.phones import normalize
from leadkit.store import Store, dumps

# What each field tends to be called in exports from CRMs, MLS tools, Instantly,
# Apollo and hand-made sheets. Compared after lowercasing and stripping
# everything but letters.
SYNONYMS = {
    "name": ["name", "fullname", "agent", "agentname", "contactname", "listingagent",
             "listingagentname", "realtor", "realtorname"],
    "first_name": ["firstname", "first", "fname", "givenname"],
    "last_name": ["lastname", "last", "lname", "surname", "familyname"],
    "email": ["email", "emailaddress", "agentemail", "workemail", "mail"],
    "phone": ["phone", "phonenumber", "mobile", "mobilephone", "cell", "cellphone", "agentphone",
              "directphone", "officephone", "phone1", "telephone", "tel"],
    "brokerage": ["brokerage", "company", "companyname", "office", "officename", "broker",
                  "brokeragename", "agency", "firm", "organization"],
    "city": ["city", "cities", "market", "location", "area", "officecity"],
}
FIELDS = list(SYNONYMS)


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.lower())


def read_rows(path: Path) -> tuple[list[str], list[dict]]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        sheet = load_workbook(path, read_only=True, data_only=True).active
        values = [["" if v is None else str(v).strip() for v in row]
                  for row in sheet.iter_rows(values_only=True)]
        header = values[0] if values else []
        return header, [dict(zip(header, r)) for r in values[1:] if any(r)]
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        rows = [{k: (v or "").strip() for k, v in r.items() if k} for r in reader]
        return list(reader.fieldnames or []), rows


def detect_columns(header: list[str]) -> dict[str, str]:
    """{field: column}. Exact synonym matches win; then a header that
    contains a synonym (e.g. "Agent Mobile Phone")."""
    mapping: dict[str, str] = {}
    normed = {h: _norm(h) for h in header}
    for field, words in SYNONYMS.items():
        for h, n in normed.items():
            if n in words and h not in mapping.values():
                mapping[field] = h
                break
    for field, words in SYNONYMS.items():
        if field in mapping or field in ("first_name", "last_name"):
            continue
        for h, n in normed.items():
            # Short words ("name", "city") are too common inside other headers.
            if h not in mapping.values() and any(len(w) > 4 and w in n for w in words):
                mapping[field] = h
                break
    return mapping


def describe_mapping(header: list[str], mapping: dict[str, str]) -> str:
    lines = [f"  {field:<11} <- {mapping[field]!r}" for field in FIELDS if field in mapping]
    missing = [f for f in ("name", "email", "phone", "brokerage", "city") if f not in mapping
               and not (f == "name" and "first_name" in mapping)]
    if missing:
        lines.append(f"  not found:  {', '.join(missing)}")
    unused = [h for h in header if h not in mapping.values()]
    if unused:
        lines.append(f"  kept as-is: {', '.join(unused[:12])}{' ...' if len(unused) > 12 else ''}")
    return "\n".join(lines)


def load(store: Store, path: Path, mapping: dict[str, str]) -> dict:
    """Load rows as agents. Rows for the same person (same email, or same
    name when there is no email) merge into one; the first non-empty value
    of each field wins."""
    header, rows = read_rows(path)
    if "name" not in mapping and "first_name" not in mapping:
        raise ValueError("No name column. Pass --map name=\"<column>\" (or first_name and last_name).")

    merged: dict[str, dict] = {}
    by_email: dict[str, str] = {}
    skipped = 0
    for row in rows:
        get = lambda f: (row.get(mapping[f], "") if f in mapping else "").strip()
        name = get("name") or f"{get('first_name')} {get('last_name')}".strip()
        email = get("email").lower()
        key = by_email.get(email) if email else None
        key = key or name_key(name)
        if not key:
            skipped += 1
            continue
        if key in merged:
            agent = merged[key]
            agent["rows_merged"] += 1
            for f in ("email", "brokerage", "city"):
                if not agent[f] and get(f):
                    agent[f] = get(f).lower() if f == "email" else get(f)
            # A number that can exist beats one that can't; the other is kept.
            phone = get("phone")
            if phone and phone != agent["phone"]:
                if not agent["phone"] or (normalize(agent["phone"]) is None and normalize(phone)):
                    phone, agent["phone"] = agent["phone"], phone
                if phone:
                    agent["other_phones"].append(phone)
        else:
            first, last = (get("first_name"), get("last_name")) if "first_name" in mapping else split_name(name)
            merged[key] = {"key": key, "name": name, "first_name": first, "last_name": last,
                           "email": email, "phone": get("phone"), "brokerage": get("brokerage"),
                           "city": get("city"), "other_phones": [], "rows_merged": 1, "extra": row}
        if email:
            by_email[email] = key

    store.write_many("""
        INSERT OR IGNORE INTO agents (key, name, first_name, last_name, email, email_status,
            email_source, phone, other_phones, brokerage, cities, active_listings, rows_merged, extra)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, [(a["key"], a["name"], a["first_name"], a["last_name"], a["email"] or None,
           "imported" if a["email"] else None, "your list" if a["email"] else None,
           a["phone"] or None, "; ".join(a["other_phones"]) or None, a["brokerage"] or None, a["city"] or None, None,
           a["rows_merged"], dumps(a["extra"])) for a in merged.values()])
    return {"rows": len(rows), "agents": len(merged), "duplicates_merged": len(rows) - len(merged) - skipped,
            "skipped_no_name": skipped}
