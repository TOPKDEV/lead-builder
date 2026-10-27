"""Active listings and their listing agents, from Realtor.com through Monid.

Each listing comes with its agent's name, phone, email and brokerage, so a
search per price band is the whole build: no page per listing, no proxy.

What the real answers showed (2026-10-27):

- A search returns at most 25 listings, in no particular order, with no
  total and no paging. So an area is cut into price bands small enough to
  come back under the cap; a full answer means the band may hold more.
- Realtor.com bills every listing returned, so splitting a full band blind
  pays for most listings twice. Zillow's search (AnyAPI, also through
  Monid) costs a flat $0.0008 a call, so it does the cutting: bands are
  split on Zillow's answers until each holds under TARGET, then each is
  bought once from Realtor.com. That count is also what the quote is
  priced on. A band Realtor.com still fills is split and bought again.
- Asking for active listings only still returns some pending ones; they're
  dropped here, and whether a band was full is judged before that.
- Price and property type filters are honoured; a county works as the
  location ("Broward County, FL"), as do cities and ZIP codes.
- A run can say COMPLETED with the tool's refusal inside (Zillow's "ibex"
  route takes one property type at most); that's read as an error, not as
  an empty band. A count that's wrong anyway costs little: the band is
  still searched on Realtor.com, and split if it comes back full.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from leadkit import monid, settings
from leadkit.models import Listing

FULL = 25
TARGET = 20     # a band Zillow counts this many in is cut before buying
# Seed bands, fine through the dense $100k-900k range.
PRICE_BANDS = [
    (0, 150_000), (150_001, 250_000), (250_001, 350_000), (350_001, 450_000),
    (450_001, 550_000), (550_001, 700_000), (700_001, 900_000), (900_001, 1_200_000),
    (1_200_001, 1_800_000), (1_800_001, 3_000_000), (3_000_001, 6_000_000), (6_000_001, None),
]
_MIN_BAND = 2_000
_TOP_PRICE = 60_000_000
# The job's property types (house, condo, townhouse, multifamily) in each site's words.
REALTOR_TYPES = {1: ["single_family"], 2: ["condo_townhome", "coop"], 3: ["townhomes"],
                 4: ["multi_family", "duplex_triplex"]}
ZILLOW_TYPES = {1: ["singleFamily"], 2: ["condo"], 3: ["townhome"], 4: ["multiFamily"]}
SKIP_STATUSES = ("pending", "sold", "off_market", "coming_soon")


def _items(res: dict) -> list[dict]:
    """The listings in an answer. A run can say COMPLETED while the tool
    behind it refused (an HTTP error inside, billed $0); that's an error,
    not an empty area."""
    answer = res.get("providerResponse") or {}
    if (res.get("status") or "").upper() in ("FAILED", "BLOCKED") or (answer.get("httpStatus") or 200) >= 400:
        raise RuntimeError(f"the listing search didn't run: {str(answer.get('error') or res.get('status'))[:200]}")
    items = ((res.get("output") or {}).get("data") or {}).get("items")
    return [i for i in items or [] if isinstance(i, dict)]


def _cost(res: dict, fallback: float) -> float:
    value = (res.get("cost") or {}).get("value")
    return float(value) if value is not None else fallback


def splittable(lo: int, hi: int | None) -> bool:
    return lo < _TOP_PRICE if hi is None else hi - lo >= _MIN_BAND


def split(lo: int, hi: int | None) -> list[tuple[int, int | None]]:
    mid = max(lo * 2, 1_000_000) if hi is None else (lo + hi) // 2
    return [(lo, mid), (mid + 1, hi)]


def seed_bands(min_price: int | None, max_price: int | None) -> list[tuple[int, int | None]]:
    out = []
    for lo, hi in PRICE_BANDS:
        if max_price is not None and lo > max_price:
            continue
        if min_price is not None and hi is not None and hi < min_price:
            continue
        lo = max(lo, min_price or 0)
        hi = max_price if hi is None else min(hi, max_price) if max_price is not None else hi
        out.append((lo, hi))
    return out


def zillow_count(location: str, lo: int, hi: int | None, types, usage=None) -> int:
    """Active listings Zillow has in a band, up to FULL."""
    body = {"location": location, "minPrice": lo, "limit": FULL,
            "homeTypes": sorted({t for k in types for t in ZILLOW_TYPES.get(k, [])})}
    if hi is not None:
        body["maxPrice"] = hi
    for attempt in range(2):
        res = monid.run("anyapi", "/zillow.search", body, timeout=120)
        if usage:
            usage("zillow", 1, _cost(res, settings.PRICES["zillow_search"]))
        try:
            return len(_items(res))
        except RuntimeError:
            if attempt:
                raise


def plan(location: str, min_price: int | None, max_price: int | None, types,
         usage=None, workers: int = 8) -> list[tuple[int, int | None, int]]:
    """[(lo, hi, expected)]: price bands that each come back from one search,
    with Zillow's count for each, lowest first. Neighbouring bands with few
    listings are merged, since every search has a fee of its own."""
    pending, done = seed_bands(min_price, max_price), []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        while pending:
            counts = list(ex.map(lambda b: zillow_count(location, b[0], b[1], types, usage), pending))
            again = []
            for (lo, hi), n in zip(pending, counts):
                if n >= TARGET and splittable(lo, hi):
                    again += split(lo, hi)
                else:
                    done.append((lo, hi, n))
            pending = again
    done.sort(key=lambda b: b[0])
    merged: list[tuple[int, int | None, int]] = []
    for lo, hi, n in done:
        if merged and merged[-1][2] + n < TARGET and merged[-1][1] is not None and merged[-1][1] + 1 >= lo:
            mlo, _, mn = merged.pop()
            merged.append((mlo, hi, mn + n))
        else:
            merged.append((lo, hi, n))
    return merged


def to_listing(item: dict, market: str) -> Listing:
    return Listing(
        mls_number=str(item.get("listingId") or item.get("propertyId") or item.get("mlsId") or ""),
        address=item.get("addressLine") or "", market=market, city=item.get("city") or "",
        state=item.get("state") or "", zip_code=str(item.get("postalCode") or ""),
        price=item.get("price"), beds=item.get("beds"), baths=item.get("baths"), sqft=item.get("sqft"),
        days_on_market=item.get("daysOnMarket"), property_type=item.get("propertyType") or "",
        listing_url=item.get("url") or "", agent_name=(item.get("agentName") or "").strip() or None,
        agent_phone=item.get("agentPhone") or None, agent_brokerage=item.get("brokerName") or None,
        agent_email=(item.get("agentEmail") or "").strip().lower() or None)


def fetch(location: str, lo: int, hi: int | None, types, market: str, usage=None) -> tuple[list[Listing], bool]:
    """(the active listings in a band, whether the answer was full)."""
    body = {"location": location, "priceMin": lo, "limit": FULL, "searchStatuses": ["for_sale"],
            "propertyTypes": sorted({t for k in types for t in REALTOR_TYPES.get(k, [])})}
    if hi is not None:
        body["priceMax"] = hi
    res = monid.run("anyapi", "/realtor.search", body, timeout=240)
    items = _items(res)
    if usage:
        usage("realtor", 1, _cost(res, settings.PRICES["realtor_call"]
                                  + len(items) * settings.PRICES["realtor_listing"]))
    keep = [to_listing(i, market) for i in items
            if str(i.get("status") or "").lower() not in SKIP_STATUSES and (i.get("listingId") or i.get("propertyId"))]
    return keep, len(items) >= FULL
