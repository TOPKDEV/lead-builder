"""Running a job: each step in order, each one resumable.

Every step picks up only the rows it hasn't finished, so stopping and
re-running is always safe and never pays twice. A step stops the whole run
on anything that would otherwise fail every remaining row quietly: an
empty balance, a rejected key, or spend passing the approved budget. Ordinary per-agent errors leave that agent for next time.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from collections import Counter

from leadkit import cellfinder, emailverify, estimate, monid, phones, realtor, rollup, search, settings, skiptrace, volume
from leadkit.emails import headless
from leadkit.emails.finder import find_email
from leadkit.http import Session
from leadkit.jobs import Job
from leadkit.models import Market
from leadkit.store import Store, dumps

logger = logging.getLogger(__name__)


class Stop(RuntimeError):
    """Something that would fail every remaining row. Ends the run with a
    message the user can act on."""


FATAL = (Stop, search.OutOfCredits, phones.PhoneCheckError, emailverify.VerifyError, monid.MonidError)


class Meter:
    """Counts billable calls and enforces the approved budget."""

    def __init__(self, store: Store, budget: float | None):
        self.store = store
        self.limit = budget * settings.BUDGET_OVERRUN if budget else None
        self.lock = threading.Lock()

    def __call__(self, provider: str, calls: int = 1, units: float = 0):
        self.store.add_usage(provider, calls, units)
        if self.limit is not None:
            spent = sum(estimate.spend(self.store.usage()).values())
            if spent > self.limit:
                raise Stop(f"Spend reached ${spent:,.2f}, past the approved budget plus 25% "
                           f"(${self.limit:,.2f}). Re-quote and approve a higher budget to continue.")


def friendly(e: Exception) -> str:
    """An error in words a non-programmer can act on. The full text stays in the log."""
    import requests
    host = ""
    m = re.search(r"host='([^']+)'", str(e))
    if m:
        host = m.group(1)
    if isinstance(e, requests.ConnectionError):
        return f"couldn't reach {host or 'the service'}. Check your internet connection"
    if isinstance(e, requests.Timeout):
        return f"{host or 'the service'} took too long to answer"
    if isinstance(e, requests.HTTPError) and e.response is not None:
        code = e.response.status_code
        site = host or e.response.url.split("/")[2]
        if code in (401, 403):
            return f"{site} rejected the key. Re-paste it on the Keys page"
        if code == 429:
            return f"{site} says we're going too fast; try again in a few minutes"
        return f"{site} answered with an error ({code})"
    return str(e)[:200]


def _pool(items, workers: int, fn, label: str):
    """Run fn over items with a worker pool, reporting progress, stopping
    everything on a fatal error, and after 30 errors in a row (which means
    something is broken, not that 30 agents are hard)."""
    total = len(items)
    if not total:
        print(f"  {label}: nothing left to do")
        return
    print(f"  {label}: {total:,} to do, {workers} at a time")
    stop = threading.Event()
    state = {"done": 0, "streak": 0, "failed": 0, "last_error": "", "fatal": None}
    lock = threading.Lock()
    started = time.monotonic()

    def run(item):
        if stop.is_set():
            return
        try:
            fn(item)
            ok = True
        except FATAL as e:
            with lock:
                state["fatal"] = state["fatal"] or e
            stop.set()
            return
        except Exception as e:
            logger.debug(f"{label} error: {e}")
            ok = False
            with lock:
                state["failed"] += 1
                state["last_error"] = friendly(e)
                if state["failed"] <= 5:  # a few for the log, not thousands
                    print(f"    error: {e}", flush=True)
        with lock:
            state["done"] += 1
            state["streak"] = 0 if ok else state["streak"] + 1
            if state["streak"] >= 30:
                state["fatal"] = Stop(f"{label}: 30 failures in a row. Check the connection and keys "
                                      "(python leads.py doctor), then run again; finished work is kept.")
                stop.set()
            n = state["done"]
            if n % 50 == 0 or n == total:
                rate = n / max(1e-6, time.monotonic() - started)
                left = (total - n) / rate / 60 if rate else 0
                print(f"    {n:,}/{total:,}  ({rate:.1f}/s, ~{left:.0f} min left)", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(run, i) for i in items]):
            f.result()
    if state["fatal"]:
        raise state["fatal"]
    if state["failed"] and state["failed"] == total:
        # Every single one failing is a broken key or connection, not bad luck.
        raise Stop(f"{label}: none of them could be done: {state['last_error']}.")
    if state["failed"]:
        print(f"  {state['failed']:,} couldn't be done this time (a site or service didn't answer); "
              "running again retries only those.")


# --- steps ------------------------------------------------------------------

def step_count(job: Job, store: Store, meter: Meter, limit: int | None):
    """Cut each area into price bands of under ~20 listings, counting on
    Zillow (fractions of a cent). The quote is priced on these counts."""
    for m in job.markets:
        market = Market(label=m["label"])
        if store.market_done(market.label):
            continue
        print(f"  counting active listings in {market.label}...", flush=True)
        bands = realtor.plan(market.label, job.min_price, job.max_price, job.property_types,
                             usage=meter, workers=settings.LISTING_WORKERS)
        store.save_bands(market.label, bands)
        store.mark_market_done(market.label, sum(n for _, _, n in bands))
        print(f"    about {sum(n for _, _, n in bands):,} active listings, in {len(bands):,} price bands")
        if not any(n for _, _, n in bands):
            print("    (none: check the spelling, e.g. \"Broward County, FL\")")


def step_listings(job: Job, store: Store, meter: Meter, limit: int | None):
    """Each band once from Realtor.com: its listings with their agents. A
    band that comes back full is split, and the halves are searched too."""
    lock = threading.Lock()

    def fetch(band):
        listings, full = realtor.fetch(band["market"], band["lo"], band["hi"], job.property_types,
                                       band["market"], usage=meter)
        store.save_listings(listings)
        status = "split" if full and realtor.splittable(band["lo"], band["hi"]) else "done"
        with lock:
            if status == "split":
                store.save_bands(band["market"], [(lo, hi, None) for lo, hi in realtor.split(band["lo"], band["hi"])])
            store.write("UPDATE bands SET status = ? WHERE market = ? AND lo = ?", (status, band["market"], band["lo"]))

    def pending():
        return [dict(r) for r in store.q("SELECT * FROM bands WHERE status IS NULL ORDER BY market, lo")]

    bands = pending()
    if limit:  # a trial: enough listings for about `limit` agents
        bands = bands[:math.ceil(limit * settings.LISTINGS_PER_AGENT / realtor.TARGET)]
    while bands:
        _pool(bands, settings.LISTING_WORKERS, fetch, "listing searches")
        left = pending()
        if limit or [(b["market"], b["lo"]) for b in left] == [(b["market"], b["lo"]) for b in bands]:
            break  # a trial stops after one round, and a round that got nowhere would again
        bands = left
    print(f"  {store.one('SELECT COUNT(*) FROM listings') or 0:,} active listings so far")


def step_agents(job: Job, store: Store, meter: Meter, limit: int | None):
    summary = rollup.build_agents(store, job.min_listings)
    emails = store.one("SELECT COUNT(*) FROM agents WHERE email_source = 'realtor.com listing'") or 0
    print(f"  {summary['agents']:,} unique agents from {summary['listings']:,} listings, "
          f"{emails:,} with an email from their listings"
          + (f" ({summary['below_min']:,} under {job.min_listings} listings left out)" if summary["below_min"] else ""))


def _first_city(agent: dict) -> str:
    return (agent.get("cities") or "").split(";")[0].strip()


def step_emails(job: Job, store: Store, meter: Meter, limit: int | None):
    if not search.provider():
        raise Stop("Finding emails needs the Monid key. Run: python leads.py keys")
    agents = store.agents("email IS NULL AND email_status IS NULL")[: limit or None]
    local = threading.local()

    def find(a):
        if not getattr(local, "session", None):
            # Brokerage pages are fetched from this computer: their
            # firewalls trust a home connection more than a data centre.
            local.session = Session()
        hit = find_email(a["name"], a["brokerage"] or "", _first_city(a), local.session, usage=meter)
        if hit:
            store.update_agent(a["key"], email=hit["email"], email_source=hit["source"], email_status="found")
        else:
            store.update_agent(a["key"], email_status="not_found")

    _pool(agents, settings.EMAIL_WORKERS, find, "email search")


def step_deep_emails(job: Job, store: Store, meter: Meter, limit: int | None):
    if not headless.available():
        print("  deep email pass skipped: Chrome automation isn't installed. To add it:\n"
              "    .venv/bin/pip install patchright && .venv/bin/patchright install chromium")
        return
    agents = store.agents("email_status = 'not_found'")[: limit or None]
    local = threading.local()
    sessions = []

    def find(a):
        if not getattr(local, "browser", None):
            local.browser = headless.HeadlessSession(str(job.dir / f"chrome-{threading.get_ident()}"))
            sessions.append(local.browser)
        hit = find_email(a["name"], a["brokerage"] or "", _first_city(a), local.browser,
                         usage=meter, max_pages=3, max_subpages=1)
        if hit:
            store.update_agent(a["key"], email=hit["email"], email_source=hit["source"], email_status="found")
        else:
            store.update_agent(a["key"], email_status="exhausted")

    try:
        _pool(agents, settings.HEADLESS_WORKERS, find, "deep email search (Chrome)")
    finally:
        for s in sessions:
            s.close()


def step_verify_emails(job: Job, store: Store, meter: Meter, limit: int | None):
    if not emailverify.configured():
        raise Stop("Verifying emails needs the Monid key. Run: python leads.py keys")
    agents = store.agents("email IS NOT NULL AND email_check IS NULL")[: limit or None]

    def check(a):
        meter("email", 1, emailverify.price())
        store.update_agent(a["key"], email_check=emailverify.verify(a["email"]))

    _pool(agents, settings.LOOKUP_WORKERS, check, "email verification")


def step_verify_phones(job: Job, store: Store, meter: Meter, limit: int | None):
    scope, params = phones.scope_sql(job)
    agents = store.agents(f"phone IS NOT NULL AND phone_status IS NULL AND {scope}", params)
    if job.phones_for == "producers":
        agents = [a for a in agents if not volume.is_listing_mill(a["seller_deals_year"], a["brokerage"])]
    agents = agents[: limit or None]
    # The free check first: numbers that cannot exist never reach Twilio.
    paid = []
    for a in agents:
        e164 = phones.normalize(a["phone"])
        if e164 is None:
            store.update_agent(a["key"], phone_valid=0, phone_status="invalid_format")
        else:
            store.update_agent(a["key"], phone_e164=e164)
            paid.append({**a, "phone_e164": e164})
    print(f"  {len(agents) - len(paid):,} numbers failed the free format check")
    # Also free: a number on two or more agents is an office line, whatever
    # Twilio would say about it, so it isn't worth paying to look up.
    counts = Counter(r["phone_e164"] for r in store.q("SELECT phone_e164 FROM agents WHERE phone_e164 IS NOT NULL"))
    shared = [a for a in paid if counts[a["phone_e164"]] >= 2]
    for a in shared:
        store.update_agent(a["key"], phone_valid=1, phone_status="shared")
    paid = [a for a in paid if counts[a["phone_e164"]] < 2]
    print(f"  {len(shared):,} numbers are shared by several agents (office lines), no lookup needed")
    if not phones.configured():
        raise Stop("Checking phones needs the Monid key. Run: python leads.py keys")

    def save(a, r):
        store.update_agent(a["key"], phone_valid=int(r["valid"]), phone_type=r["type"],
                           phone_carrier=r["carrier"], phone_status="checked",
                           phone_caller_name=r["caller_name"], phone_caller_type=r["caller_type"],
                           phone_trace=dumps(r["trace"]) if r.get("trace") else None,
                           phone_name_match=phones.name_match(r["caller_name"], a["first_name"] or "",
                                                              a["last_name"] or ""))

    def check(a):
        save(a, phones.lookup(a["phone_e164"], usage=meter))

    _pool(paid, settings.LOOKUP_WORKERS, check, "phone line type")
    if "fix_phones" not in job.addons:
        print("\n  " + estimate.format_fix_summary(job, estimate.fix_summary(job)).replace("\n", "\n  "))


def step_volume(job: Job, store: Store, meter: Meter, limit: int | None):
    if not settings.MONID_API_KEY:
        raise Stop("The homes.com volume check needs MONID_API_KEY in .env.")
    agents = store.agents("volume_status IS NULL")[: limit or None]

    def check(a):
        r = volume.check(a["name"], _first_city(a) or None, usage=meter)
        store.update_agent(a["key"], volume_status=r["status"], homes_profile=r.get("profile"),
                           seller_deals_year=r.get("seller_deals_year"), deals_year=r.get("deals_year"),
                           seller_volume_year=r.get("seller_volume_year"),
                           seller_avg_price=r.get("seller_avg_price"),
                           closed_sales_5yr=r.get("closed_sales_5yr"), homes_phone=r.get("homes_phone"))

    _pool(agents, settings.LOOKUP_WORKERS, check, "homes.com volume")


def step_fix_phones(job: Job, store: Store, meter: Meter, limit: int | None):
    if not phones.configured():
        raise Stop("Fixing phones needs the Monid key. Run: python leads.py keys")
    todo, taken = phones.to_fix(job, store)
    lock = threading.Lock()
    todo = todo[: limit or None]
    local = threading.local()
    # A skip trace by name has to be in the agent's state to be them; an
    # imported list rarely says which state, so its area codes decide.
    state = skiptrace.home_state(taken)

    def fix(a):
        if not getattr(local, "session", None):
            local.session = Session()
        with lock:
            exclude = set(taken)
        cell = cellfinder.find_cell(a, exclude, local.session, usage=meter, state=skiptrace.state_of(a, state))
        if not cell:
            store.update_agent(a["key"], phone_fix="not_found", phone_note=a["reason"])
            return
        found = dict(phone_valid=1, phone_type=cell["type"], phone_carrier=cell["carrier"], phone_status="checked",
                     phone_caller_name=cell["caller_name"], phone_caller_type=cell["caller_type"],
                     phone_name_match=cell["name_match"], phone_found_on=cell["source"])
        if cell.get("confirmed"):
            # The number on file is theirs after all: their record lists it as their cell.
            store.update_agent(a["key"], phone_fix="confirmed", phone_note=a["reason"], **found)
            return
        with lock:
            if cell["e164"] in taken:  # another worker just gave it to someone else
                store.update_agent(a["key"], phone_fix="not_found", phone_note=a["reason"])
                return
            taken.add(cell["e164"])
        store.update_agent(a["key"], phone_fix="replaced", phone_note=a["reason"], phone_original=a["phone"],
                           phone=phones.pretty(cell["e164"]), phone_e164=cell["e164"], **found)

    _pool(todo, settings.LOOKUP_WORKERS, fix, "finding real cell numbers")
    replaced = store.one("SELECT COUNT(*) FROM agents WHERE phone_fix = 'replaced'") or 0
    print(f"  {replaced:,} numbers replaced with the agent's own cell so far")


STEPS = {
    "count": step_count, "listings": step_listings, "agents": step_agents, "emails": step_emails,
    "deep_emails": step_deep_emails, "verify_emails": step_verify_emails,
    "verify_phones": step_verify_phones, "volume": step_volume, "fix_phones": step_fix_phones,
}


def run(job: Job, only: str | None = None, limit: int | None = None) -> None:
    store = job.store()
    meter = Meter(store, job.approved_budget)
    try:
        for step in job.steps():
            if only and step != only:
                continue
            print(f"\n== {step} ==", flush=True)
            STEPS[step](job, store, meter, limit)
            # Fresh files after every step, so a long run is useful before it
            # ends; counting finds no agents, so there's nothing to write yet.
            if step != "count":
                from leadkit import export
                export.export(job)
    finally:
        spent = estimate.spend(store.usage())
        print(f"\nSpent on this job so far: ${sum(spent.values()):,.2f}"
              + (" (" + ", ".join(f"{k} ${v:,.2f}" for k, v in spent.items()) + ")" if spent else ""))
        store.close()
