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

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

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


def zillow_query(location: str, types=None, lo: int | None = None, hi: int | None = None,
                 usage=None, slow_fallback: bool = False) -> list[dict] | None:
    """Zillow's search, up to FULL listings, or None if it couldn't answer.

    Its "ibex" route answers in 5-8 s but takes one home type at most, so
    with several it's asked about every kind of home (a slight overcount,
    which only makes bands a little smaller). It's tried twice. The default
    route takes all the types, but on 2026-10-27 it took 35-65 s and often
    failed, so it's only the fallback when `slow_fallback` is set (the area
    check, where one answer matters more than speed). For a count, a band
    that gets no answer is searched on Realtor.com anyway."""
    wanted = sorted({t for k in (types or []) for t in ZILLOW_TYPES.get(k, [])})
    base = {"location": location, "limit": FULL}
    if lo is not None:
        base["minPrice"] = lo
    if hi is not None:
        base["maxPrice"] = hi
    for route in ("ibex", "ibex") + (("default",) if slow_fallback else ()):
        body = dict(base)
        if route == "ibex":
            body["route"] = "ibex"
            if len(wanted) == 1:
                body["homeTypes"] = wanted
        elif wanted:
            body["homeTypes"] = wanted
        try:
            # The fast route answers in 5-8 s when it's well; don't sit on it.
            res = monid.run("anyapi", "/zillow.search", body, timeout=25 if route == "ibex" else 90, attempts=1)
        except monid.MonidError:
            raise
        except Exception:
            continue
        if usage:
            usage("zillow", 1, _cost(res, settings.PRICES["zillow_search"]))
        try:
            return _items(res)
        except RuntimeError:
            continue
    return None


def zillow_count(location: str, lo: int, hi: int | None, types, usage=None) -> int | None:
    """Active listings Zillow has in a band, up to FULL; None if it couldn't say."""
    items = zillow_query(location, types, lo, hi, usage)
    return None if items is None else len(items)


def plan(location: str, min_price: int | None, max_price: int | None, types,
         usage=None, workers: int = 8, on_count=None) -> list[tuple[int, int | None, int | None]]:
    """[(lo, hi, expected)]: price bands that each come back from one search,
    with Zillow's count for each, lowest first. Neighbouring bands with few
    listings are merged, since every search has a fee of its own. A band
    Zillow couldn't count has expected None: it's searched on Realtor.com
    anyway and split there if it comes back full."""
    done = []
    # A queue, not waves: a band's halves are counted as soon as it's split,
    # so one slow answer (Zillow sometimes takes a minute) holds up only itself.
    with ThreadPoolExecutor(max_workers=workers) as ex:
        running = {ex.submit(zillow_count, location, lo, hi, types, usage): (lo, hi)
                   for lo, hi in seed_bands(min_price, max_price)}
        while running:
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                (lo, hi), n = running.pop(future), future.result()
                splitting = n is not None and n >= TARGET and splittable(lo, hi)
                if on_count:
                    on_count(lo, hi, n, splitting)
                if splitting:
                    for half in split(lo, hi):
                        running[ex.submit(zillow_count, location, *half, types, usage)] = half
                else:
                    done.append((lo, hi, n))
    done.sort(key=lambda b: b[0])
    merged: list[tuple[int, int | None, int | None]] = []
    for lo, hi, n in done:
        if (merged and n is not None and merged[-1][2] is not None and merged[-1][2] + n < TARGET
                and merged[-1][1] is not None and merged[-1][1] + 1 >= lo):
            mlo, _, mn = merged.pop()
            merged.append((mlo, hi, mn + n))
        else:
            merged.append((lo, hi, n))
    return merged


def _brokerage(name) -> str | None:
    """Realtor.com prints "UNKNOWN NAME" when it doesn't have one."""
    name = (name or "").strip()
    return None if not name or name.upper() in ("UNKNOWN", "UNKNOWN NAME", "N/A", "NONE") else name


def to_listing(item: dict, market: str) -> Listing:
    return Listing(
        mls_number=str(item.get("listingId") or item.get("propertyId") or item.get("mlsId") or ""),
        address=item.get("addressLine") or "", market=market, city=item.get("city") or "",
        state=item.get("state") or "", zip_code=str(item.get("postalCode") or ""),
        price=item.get("price"), beds=item.get("beds"), baths=item.get("baths"), sqft=item.get("sqft"),
        days_on_market=item.get("daysOnMarket"), property_type=item.get("propertyType") or "",
        listing_url=item.get("url") or "", agent_name=(item.get("agentName") or "").strip() or None,
        agent_phone=item.get("agentPhone") or None, agent_brokerage=_brokerage(item.get("brokerName")),
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
