"""What a person can do with a job, shared by the command line (leads.py)
and the app (app.py), so the two can never disagree.

Each function returns data and raises ActionError with a sentence the person
can act on; the callers decide how to show it.
"""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

from leadkit import estimate, importer, jobs, settings
from leadkit.jobs import ADDONS, Job
from leadkit.store import now

PROPERTY_TYPE_NAMES = {"house": 1, "condo": 2, "townhouse": 3, "multifamily": 4}
PHONE_SCOPES = ("all", "emails", "producers")


class ActionError(ValueError):
    pass


def parse_addons(chosen) -> list[str]:
    """A comma string or a list; "all" and "none" work too. Returned in run
    order, with verify_phones added when fix_phones needs it."""
    if isinstance(chosen, str):
        text = chosen.strip().lower()
        chosen = [] if text in ("", "none") else list(ADDONS) if text == "all" else text.split(",")
    chosen = [a.strip().replace("-", "_") for a in chosen if a and a.strip()]
    unknown = [a for a in chosen if a not in ADDONS]
    if unknown:
        raise ActionError(f"Unknown add-on(s): {', '.join(unknown)}. Choose from: {', '.join(ADDONS)}")
    if "fix_phones" in chosen:
        # Fixing a number starts from knowing what's wrong with it.
        chosen.append("verify_phones")
    return [a for a in ADDONS if a in chosen]


# --- markets ----------------------------------------------------------------

def find_markets(query: str) -> list[dict]:
    """Counties, cities and ZIP codes matching what the person typed, from
    the built-in catalog of US places (free and instant)."""
    from leadkit import places
    return [{**p, "kind": f"{p['kind']}, {p['detail']}"} for p in places.search(" ".join(query.split()))]


def market(code: str) -> dict:
    """An area as a job stores it."""
    # The page sends a ZIP missing from the catalog as "zip:33327".
    label = " ".join((code or "").split())
    if label.lower().startswith("zip:"):
        label = label[4:].strip()
    if not label:
        raise ActionError("Name an area: a county, city or ZIP code, e.g. \"Broward County, FL\".")
    return {"label": label}


# --- creating a job ---------------------------------------------------------

def read_list(path: Path) -> dict:
    """A list's columns, the detected mapping and a short preview."""
    if not path.exists():
        raise ActionError(f"Can't find {path}")
    header, rows = importer.read_rows(path)
    if not header:
        raise ActionError(f"{path.name} looks empty.")
    return {"header": header, "rows": len(rows), "mapping": importer.detect_columns(header),
            "preview": [[r.get(h, "") for h in header] for r in rows[:5]]}


def create_job(name: str, mode: str, *, markets=(), file: str | None = None, mapping=None,
               addons=(), min_listings: int = 1, min_price=None, max_price=None, types=None,
               producer_min: int = 5, phones_for: str = "all",
               replace: bool = False) -> dict:
    name = jobs.slug(name)
    if name in jobs.all_names() and not replace:
        raise ActionError(f"A list called {name!r} already exists. Pick another name.")
    if mode not in ("build", "enrich"):
        raise ActionError("Choose: build a new list, or use one you already have.")
    if phones_for not in PHONE_SCOPES:
        raise ActionError(f"Phone checks are for one of: {', '.join(PHONE_SCOPES)}")
    addons = parse_addons(addons)
    if phones_for == "producers" and "volume" not in addons:
        raise ActionError("Phone checks for producers only needs the homes.com check too; that's how producers are found.")
    job = Job(name=name, mode=mode, addons=addons, min_listings=int(min_listings or 1),
              min_price=min_price or None, max_price=max_price or None, producer_min=int(producer_min),
              phones_for=phones_for)
    if types:
        try:
            job.property_types = [PROPERTY_TYPE_NAMES[t.strip()] for t in types]
        except KeyError:
            raise ActionError(f"Property types are: {', '.join(PROPERTY_TYPE_NAMES)}")

    result: dict = {"name": name}
    if mode == "build":
        if not markets:
            raise ActionError("Pick at least one area.")
        job.markets = [market(c) for c in markets]
    else:
        path = Path(file or "").expanduser()
        info = read_list(path)
        full = {**info["mapping"], **(mapping or {})}
        full = {f: c for f, c in full.items() if c}
        bad = [f for f, c in full.items() if f not in importer.FIELDS or c not in info["header"]]
        if bad:
            raise ActionError(f"These fields point at columns that aren't in the file: {', '.join(bad)}")
        if "name" not in full and "first_name" not in full:
            raise ActionError("Which column has the agent's name?")
        job.source_file, job.column_map = str(path.resolve()), full
    if replace and job.dir.exists():
        shutil.rmtree(job.dir)
    job.save()
    if mode == "enrich":
        result["loaded"] = importer.load(job.store(), Path(job.source_file), job.column_map)
    return result


# --- price, approval, more steps --------------------------------------------

def quote(job: Job) -> dict:
    """The price of what's left. A build counts its listings first (cents,
    on Zillow, kept so the run doesn't repeat it)."""
    if job.mode == "build":
        from leadkit import pipeline
        store = job.store()
        pending = not all(store.market_done(m["label"]) for m in job.markets)
        store.close()
        if pending:
            try:
                pipeline.run(job, only="count")
            except pipeline.FATAL as e:
                raise ActionError(f"Couldn't count listings: {e}") from e
    q = estimate.quote(job)
    job.quote = q
    job.save()
    # The budget covers the whole job, so it includes what's already spent.
    return {**q, "missing": estimate.missing_keys(job), "needs": sorted(estimate.services_needed(job)),
            "suggested_budget": max(1, round(q["spent_so_far"] + q["total"] * 1.1 + 0.5))}


def approve(job: Job, budget: float) -> None:
    if not job.quote:
        raise ActionError("Get the price first.")
    if budget <= 0:
        raise ActionError("The budget has to be more than $0.")
    job.approved_budget, job.approved_at = float(budget), now()
    job.save()


def add_steps(job: Job, addons) -> list[str]:
    """Add steps to a job, typically fix_phones once the check has shown how
    many numbers are wrong. The job then needs a fresh quote and approval."""
    added = [a for a in parse_addons(addons) if a not in job.addons]
    job.addons = [a for a in ADDONS if a in job.addons or a in added]
    job.approved_budget = None  # the new work hasn't been approved yet
    job.save()
    return added


# --- progress ---------------------------------------------------------------

def status(job: Job) -> dict:
    store = job.store()
    one = lambda sql, p=(): store.one(sql, p) or 0  # noqa: E731
    s = {
        "agents": one("SELECT COUNT(*) FROM agents"),
        "listings": one("SELECT COUNT(*) FROM listings"),
        "bands_left": one("SELECT COUNT(*) FROM bands WHERE status IS NULL"),
        "emails": one("SELECT COUNT(*) FROM agents WHERE email IS NOT NULL"),
        "searched": one("SELECT COUNT(*) FROM agents WHERE email_status IN ('found', 'not_found', 'exhausted')"),
        "verified": one("SELECT COUNT(*) FROM agents WHERE email_check = 'ok'"),
        "email_checked": one("SELECT COUNT(*) FROM agents WHERE email_check IS NOT NULL"),
        "phones": one("SELECT COUNT(*) FROM agents WHERE phone_status IS NOT NULL"),
        # Counted the way the cell-phones file picks them.
        "mobile": one("SELECT COUNT(*) FROM agents WHERE phone_type = 'mobile' "
                      "AND COALESCE(phone_name_match, '') != 'mismatch' AND COALESCE(phone_litigator, 0) = 0"),
        "probably_cells": one("SELECT COUNT(*) FROM agents WHERE phone_line = 'mobile' AND phone_type = 'unknown'"),
        "replaced": one("SELECT COUNT(*) FROM agents WHERE phone_fix = 'replaced'"),
        "fix_tried": one("SELECT COUNT(*) FROM agents WHERE phone_fix IS NOT NULL"),
        "volume": one("SELECT COUNT(*) FROM agents WHERE volume_status IS NOT NULL"),
        "producers": one("SELECT COUNT(*) FROM agents WHERE seller_deals_year >= ?", (job.producer_min,)),
        "spent": round(sum(estimate.spend(store.usage()).values()), 2),
    }
    store.close()
    # After the phone check: how many look wrong, and the price of fixing them.
    s["fix"] = estimate.fix_summary(job) if s["phones"] and "fix_phones" not in job.addons else None
    return s


def files(job: Job) -> list[dict]:
    """The newest export of each kind."""
    if not job.export_dir.exists():
        return []
    newest: dict[str, Path] = {}
    for p in sorted(job.export_dir.glob("*.csv")):
        kind = p.stem[len(job.name) + 1:].rsplit("-", 3)[0]
        newest[kind] = p
    order = ["everyone", "email-ready", "email-probably-ok", "email-ready-UNVERIFIED", "cell-phones",
             "cell-phones-probably", "producers"]
    out = []
    for kind in sorted(newest, key=lambda k: order.index(k) if k in order else 99):
        p = newest[kind]
        with p.open(encoding="utf-8", newline="") as f:
            rows = max(0, sum(1 for _ in csv.reader(f)) - 1)
        out.append({"kind": kind, "file": p.name, "rows": rows})
    return out
