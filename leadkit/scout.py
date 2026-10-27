"""Scout Monid's live catalog for tools worth using, with current prices.

Monid's catalog search (`discover`) and endpoint details (`inspect`) are free;
only running a tool costs money. So this asks the catalog about everything a
real estate media business might want (list building, contact data, outreach,
prospect research, AI photo and video) and writes what it finds, with prices,
to exports/monid-catalog-<date>.md and .csv, plus the raw answers as .json.

Monid doesn't publish the exact shape of those answers, so fields are read
by several likely names, and the raw answer is always kept.
"""
from __future__ import annotations

import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import requests

from leadkit import monid, settings

QUERIES = {
    "Building and checking the list": [
        "verify email address deliverability", "bulk email verification",
        "phone number line type mobile landline carrier lookup", "phone number validation",
        "find a person's mobile phone number", "reverse phone lookup owner name",
        "residential proxy unblock any website", "scrape any page with anti-bot bypass",
        "zillow listings", "zillow real estate agents", "realtor.com agents", "homes.com agent profile",
        "redfin listings", "property records owner", "skip tracing", "MLS listing data",
        "cheap google search results", "free web search",
    ],
    "Prospect research (who's worth pitching)": [
        "instagram profile followers posts", "tiktok profile videos", "youtube channel videos",
        "facebook ads library", "facebook page info", "google business profile reviews",
        "linkedin profile search", "real estate agent reviews", "website tech stack",
    ],
    "Outreach": [
        "send sms", "place ai phone call", "send email mailbox", "email warmup",
    ],
    "Making the media": [
        "image to video", "edit photo with ai", "virtual staging", "day to twilight photo",
        "upscale image", "remove object from photo", "text to speech voiceover", "music generation",
        "video editing captions", "3d model from photos", "floor plan",
    ],
}

_RESULT_KEYS = ("results", "endpoints", "data", "items", "matches", "output")


def _post(path: str, body: dict) -> dict:
    resp = requests.post(f"{monid.BASE}{path}", headers=monid._headers(), json=body, timeout=60)
    if resp.status_code in (401, 403):
        raise monid.MonidError("Monid rejected the key. Check MONID_API_KEY.")
    resp.raise_for_status()
    return resp.json()


def _rows(answer) -> list[dict]:
    if isinstance(answer, list):
        return [r for r in answer if isinstance(r, dict)]
    if isinstance(answer, dict):
        for k in _RESULT_KEYS:
            if k in answer:
                return _rows(answer[k])
    return []


def _get(row: dict, *names, default=""):
    flat = {re.sub(r"[^a-z]", "", k.lower()): v for k, v in row.items()}
    for n in names:
        v = flat.get(n)
        if v not in (None, "", [], {}):
            return v
    return default


def identify(row: dict) -> tuple[str, str]:
    """(provider, endpoint) from a discover row, e.g. "hunterio#/email-verifier"."""
    provider = _get(row, "provider", "providername", "providerid")
    endpoint = _get(row, "endpoint", "path", "endpointpath")
    if isinstance(provider, dict):
        provider = provider.get("name") or provider.get("id") or ""
    ident = _get(row, "id", "endpointid", "ref")
    if (not provider or not endpoint) and isinstance(ident, str) and "#" in ident:
        provider, endpoint = ident.split("#", 1)
    if endpoint and not str(endpoint).startswith("/"):
        endpoint = "/" + str(endpoint)
    return str(provider), str(endpoint)


def describe_price(info: dict) -> str:
    """Whatever the price looks like, as one readable string."""
    price = _get(info, "price", "pricing", "cost", "priceusd", "usd")
    if isinstance(price, (int, float)):
        return f"${price:g}"
    if isinstance(price, str):
        return price
    if isinstance(price, dict):
        # Monid's shape: {"type": "PER_CALL", "amount": {"value": 0.01, "currency": "USD"}}
        amount = _get(price, "amount", "usd", "price", "value", "percall")
        if isinstance(amount, dict):
            amount = amount.get("value")
        unit = {"PER_CALL": "call", "PER_RESULT": "result", "PER_UNIT_MATRIX": "call",
                "TIERED": "call"}.get(str(price.get("type")), _get(price, "unit", "per", "basis"))
        if amount not in ("", None):
            return f"${amount:g}" + (f" per {unit}" if unit else "") if isinstance(amount, (int, float)) \
                else f"${amount}" + (f" per {unit}" if unit else "")
        return json.dumps(price)[:120]
    return ""


def scout(extra: list[str] | None = None, limit: int = 12, inspect: bool = True, progress=print) -> dict:
    queries = {k: list(v) for k, v in QUERIES.items()}
    if extra:
        queries["Your own searches"] = list(extra)
    found: dict[tuple[str, str], dict] = {}
    raw: dict = {"discover": {}, "inspect": {}}
    for section, qs in queries.items():
        for q in qs:
            try:
                answer = _post("/v1/discover", {"query": q, "limit": limit})
            except requests.HTTPError as e:
                progress(f"  ! {q}: {e}")
                continue
            raw["discover"][q] = answer
            for row in _rows(answer):
                key = identify(row)
                if not all(key):
                    continue
                item = found.setdefault(key, {
                    "provider": key[0], "endpoint": key[1], "sections": set(), "queries": [],
                    "name": _get(row, "displayname", "name", "title"),
                    "summary": _get(row, "summary", "description"),
                    "price": describe_price(row), "score": _get(row, "score", "relevance", default=0)})
                item["sections"].add(section)
                item["queries"].append(q)
            progress(f"  {section}: {q}  ({len(_rows(answer))} tools)")

    if inspect:
        def look(item):
            try:
                info = _post("/v1/inspect", {"provider": item["provider"], "endpoint": item["endpoint"]})
            except Exception as e:
                return item, {"error": str(e)}
            return item, info
        with ThreadPoolExecutor(max_workers=4) as ex:
            for item, info in ex.map(look, list(found.values())):
                raw["inspect"][f"{item['provider']}#{item['endpoint']}"] = info
                if isinstance(info, dict) and "error" not in info:
                    item["price"] = describe_price(info) or describe_price(info.get("data") or {}) or item["price"]
                    item["summary"] = item["summary"] or _get(info, "summary", "description")
                    item["name"] = item["name"] or _get(info, "displayname", "name")
    return {"found": found, "raw": raw}


def write_report(result: dict) -> dict[str, str]:
    out_dir = settings.EXPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    paths = {k: str(out_dir / f"monid-catalog-{stamp}.{k}") for k in ("md", "csv", "json")}
    items = sorted(result["found"].values(), key=lambda i: (sorted(i["sections"])[0], i["provider"], i["endpoint"]))

    with open(paths["csv"], "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["section", "provider", "endpoint", "name", "price", "summary", "found_by"])
        for i in items:
            w.writerow(["; ".join(sorted(i["sections"])), i["provider"], i["endpoint"], i["name"], i["price"],
                        i["summary"], "; ".join(dict.fromkeys(i["queries"]))])

    lines = [f"# Monid catalog scout, {stamp}", "",
             f"{len(items)} tools found across {sum(len(v) for v in QUERIES.values())} searches. "
             "Prices are Monid's live prices at the time of the scout.", ""]
    for section in list(QUERIES) + ["Your own searches"]:
        rows = [i for i in items if section in i["sections"]]
        if not rows:
            continue
        lines += [f"## {section}", "", "| Tool | Provider / endpoint | Price | What it does |", "|---|---|---|---|"]
        for i in rows:
            summary = str(i["summary"]).replace("|", "/").replace("\n", " ")[:160]
            lines.append(f"| {i['name'] or i['endpoint']} | `{i['provider']}` `{i['endpoint']}` | {i['price'] or '?'} | {summary} |")
        lines.append("")
    with open(paths["md"], "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(paths["json"], "w", encoding="utf-8") as f:
        json.dump(result["raw"], f, indent=1, default=list)
    return paths
