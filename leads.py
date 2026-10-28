#!/usr/bin/env python3
"""Real estate agent lead lists: build one from active listings, or enrich
one you already have. Claude drives this (see CLAUDE.md); every command
also works by hand.

  python leads.py app                             the app: everything below, in the browser
  python leads.py keys [--job NAME]               open the page to paste the Monid key
  python leads.py doctor                          whether the key works, what can run
  python leads.py find-market "Tampa, FL"         look up a county, city or ZIP (free)
  python leads.py new NAME --build --market "Hillsborough County, FL" [--market ...] --addons all
  python leads.py new NAME --enrich my-list.csv --addons verify_phones,volume
  python leads.py quote NAME                      cost and time, before spending
  python leads.py approve NAME --budget 60        record the go-ahead
  python leads.py add NAME fix_phones             add a step (then quote and approve again)
  python leads.py run NAME                        do it (safe to stop and re-run)
  python leads.py status NAME                     progress and spend
  python leads.py export NAME                     write the CSVs again
  python leads.py jobs                            list jobs
  python leads.py scout                           Monid's catalog, searched for useful tools (free)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from leadkit import actions, estimate, importer, jobs, settings  # noqa: E402
from leadkit.jobs import ADDONS  # noqa: E402

PROPERTY_TYPE_NAMES = actions.PROPERTY_TYPE_NAMES


def _parse_addons(text: str) -> list[str]:
    try:
        return actions.parse_addons(text)
    except actions.ActionError as e:
        raise SystemExit(str(e))


def cmd_doctor(args):
    from leadkit import doctor
    doctor.run()


def cmd_keys(args):
    from leadkit import keys_page
    need = estimate.services_needed(jobs.load(args.job)) if args.job else set()
    results = keys_page.serve(need)
    print("\nKeys:")
    for name, (state, msg) in results.items():
        if state != "missing" or name in need:
            print(f"  {name:<16} {state.upper():<8} {msg}" + ("   <- needed for this job" if name in need else ""))


def cmd_set_key(args):
    from leadkit import doctor, keys_page
    if args.name not in settings.KEY_NAMES:
        raise SystemExit(f"Unknown key {args.name}. Keys: {', '.join(settings.KEY_NAMES)}")
    keys_page.write_env({args.name: args.value.strip()})
    settings.reload_keys()
    for name, keys, _ in doctor.CHECKS:
        if args.name in keys:
            state, msg = doctor.check_all()[name]
            print(f"Saved {args.name}. {name}: {state.upper()} {msg}")


def cmd_app(args):
    from leadkit import app
    app.serve(open_browser=not args.no_browser, port=args.port)


def cmd_scout(args):
    from leadkit import monid, scout
    if not settings.MONID_API_KEY:
        raise SystemExit("Scouting needs MONID_API_KEY (searching Monid's catalog is free). Run: python leads.py keys")
    print("Searching Monid's catalog (free; nothing is run or charged)...")
    try:
        result = scout.scout(extra=args.query, inspect=not args.quick)
    except monid.MonidError as e:
        raise SystemExit(str(e))
    paths = scout.write_report(result)
    print(f"\nFound {len(result['found'])} tools.")
    for kind, path in paths.items():
        print(f"  {kind:<5} {path}")


def cmd_try(args):
    """Run one Monid tool once and save exactly what it answers, so a new
    tool can be wired in from a real answer instead of a guess."""
    from leadkit import monid
    if not settings.MONID_API_KEY:
        raise SystemExit("Needs MONID_API_KEY. Run: python leads.py keys")
    params = json.loads(args.input) if args.input else {}
    body, query = (None, params) if args.query else (params, None)
    print(f"Running {args.provider} {args.endpoint} once (charged at its listed price)...")
    res = monid.run(args.provider, args.endpoint, body, query=query, timeout=300, attempts=1)
    out = settings.EXPORTS_DIR / "tool-samples"
    out.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^a-z0-9]+", "-", f"{args.provider}-{args.endpoint}".lower()).strip("-")
    path = out / f"{name}.json"
    path.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(f"Status: {res.get('status')}. Saved the full answer to {path}")


def cmd_find_market(args):
    query = " ".join(args.query)
    markets = actions.find_markets(query)
    if not markets:
        print(f"No US county, city or ZIP code matches {query!r}.")
        return
    for m in markets:
        print(f"  --market \"{m['code']}\"   ({m['kind']})")


def cmd_new(args):
    if bool(args.build) == bool(args.enrich):
        raise SystemExit("Pass exactly one of --build (from active listings) or --enrich FILE (your own list).")
    mapping = {}
    for pair in args.map or []:
        field, _, col = pair.partition("=")
        mapping[field] = col
    try:
        if args.enrich:
            info = actions.read_list(Path(args.enrich).expanduser())
            print(f"Read {info['rows']:,} rows from {Path(args.enrich).name}. Columns matched:")
            print(importer.describe_mapping(info["header"], {**info["mapping"], **mapping}))
        result = actions.create_job(
            args.name, "build" if args.build else "enrich", markets=args.market or [], file=args.enrich,
            mapping=mapping, addons=args.addons, min_listings=args.min_listings, min_price=args.min_price,
            max_price=args.max_price, types=args.types.split(",") if args.types else None,
            producer_min=args.producer_min, phones_for=args.phones_for,
            replace=args.replace)
    except actions.ActionError as e:
        raise SystemExit(str(e))
    job = jobs.load(result["name"])
    if job.mode == "build":
        print(f"Job {job.name!r}: build from active listings in")
        for m in job.markets:
            print(f"  - {m['label']}")
    else:
        summary = result["loaded"]
        print(f"\nLoaded {summary['agents']:,} agents ({summary['duplicates_merged']:,} duplicate rows merged"
              + (f", {summary['skipped_no_name']:,} rows with no name skipped" if summary["skipped_no_name"] else "")
              + ").")
    print("Add-ons: " + (", ".join(job.addons) if job.addons else "none"))
    if {"verify_phones", "fix_phones"} & set(job.addons):
        scope = {"all": "every agent", "emails": "only agents with an email", "producers": "only producers"}
        print(f"Phone checks: {scope[job.phones_for]}")
    print(f"\nNext: python leads.py quote {job.name}")


def cmd_quote(args):
    job = jobs.load(args.name)
    try:
        q = actions.quote(job)
    except actions.ActionError as e:
        raise SystemExit(f"\n{e}")
    print(estimate.format_quote(q))
    if q["missing"]:
        print("\nMISSING KEYS - these steps will stop the run until added to .env:")
        for step, need in q["missing"].items():
            print(f"  {step:<15} needs {need}")
        print(f"Add them in the browser: python leads.py keys --job {job.name}")
    print(f"\nTo go ahead: python leads.py approve {job.name} --budget {q['suggested_budget']}")


def cmd_add(args):
    job = jobs.load(args.name)
    try:
        added = actions.add_steps(job, args.addons)
    except actions.ActionError as e:
        raise SystemExit(str(e))
    print(f"Added: {', '.join(added) or 'nothing new'}. Next: python leads.py quote {job.name}")


def cmd_approve(args):
    job = jobs.load(args.name)
    try:
        actions.approve(job, args.budget)
    except actions.ActionError as e:
        raise SystemExit(f"{e} (python leads.py quote {job.name})")
    print(f"Approved up to ${args.budget:,.2f} (the run stops itself at ${args.budget * settings.BUDGET_OVERRUN:,.2f}).")
    print(f"Next: python leads.py run {job.name}")


def cmd_run(args):
    from leadkit import export, pipeline
    job = jobs.load(args.name)
    if job.approved_budget is None:
        raise SystemExit(f"Not approved yet. Run `python leads.py quote {job.name}`, "
                         f"then `python leads.py approve {job.name} --budget <dollars>`.")
    if not args.limit and not args.only:
        # A full run ends any trial the app started; its page stops offering "run the rest".
        (job.dir / "run.limit").unlink(missing_ok=True)
    try:
        pipeline.run(job, only=args.only, limit=args.limit)
    except pipeline.FATAL as e:
        print(f"\nSTOPPED: {e}\nEverything finished so far is saved; fix that and run the same command again.")
        _export(job, export)
        sys.exit(2)
    _export(job, export)


def _export(job, export):
    files = export.export(job)
    print("\nFiles:")
    for kind, (path, n) in files.items():
        print(f"  {n:>7,}  {path}")


def cmd_status(args):
    job = jobs.load(args.name)
    c = actions.status(job)
    print(f"Job {job.name} ({job.mode}), add-ons: {', '.join(job.addons) or 'none'}")
    if job.mode == "build":
        print(f"  listings      {c['listings']:,} in, {c['bands_left']:,} searches to go")
    print(f"  agents        {c['agents']:,}")
    if "emails" in job.addons or c["emails"]:
        print(f"  emails        {c['emails']:,} found ({c['searched']:,} searched)")
    if "verify_emails" in job.addons:
        print(f"  verified      {c['verified']:,} deliverable of {c['email_checked']:,} checked")
    if "verify_phones" in job.addons:
        print(f"  phones        {c['phones']:,} checked, {c['mobile']:,} mobile")
    if "fix_phones" in job.addons:
        print(f"  fixed phones  {c['replaced']:,} replaced with their own cell, of {c['fix_tried']:,} tried")
    if "volume" in job.addons:
        print(f"  homes.com     {c['volume']:,} checked, {c['producers']:,} sold {job.producer_min}+ last year")
    budget = f" of ${job.approved_budget:,.2f} approved" if job.approved_budget else ""
    print(f"  spent         ${c['spent']:,.2f}{budget}")
    if c["fix"]:
        print("\n" + estimate.format_fix_summary(job, c["fix"]))


def cmd_export(args):
    from leadkit import export
    _export(jobs.load(args.name), export)


def cmd_jobs(args):
    for name in jobs.all_names():
        job = jobs.load(name)
        print(f"{name:<30} {job.mode:<7} {'approved' if job.approved_budget else 'not approved':<13} {', '.join(job.addons)}")


def main():
    # Windows consoles and redirected logs default to an old code page, where
    # printing "→" or "✓" fails; UTF-8 everywhere.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=settings.env("LOG_LEVEL", "WARNING"), format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("app", help="open the app in the browser")
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--port", type=int, default=0)
    s.set_defaults(fn=cmd_app)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("keys", help="open a page in the browser to paste and test API keys")
    s.add_argument("--job", help="highlight the keys this job needs")
    s.set_defaults(fn=cmd_keys)
    s = sub.add_parser("set-key", help="save one key to .env and test it")
    s.add_argument("name")
    s.add_argument("value")
    s.set_defaults(fn=cmd_set_key)
    s = sub.add_parser("scout", help="search Monid's catalog (free) for useful tools, with live prices")
    s.add_argument("--query", action="append", help="also search for this (repeatable)")
    s.add_argument("--quick", action="store_true", help="skip the per-tool details (prices may be missing)")
    s.set_defaults(fn=cmd_scout)
    s = sub.add_parser("try", help="run one Monid tool once and save its full answer")
    s.add_argument("provider")
    s.add_argument("endpoint")
    s.add_argument("input", nargs="?", help='the input as JSON, e.g. \'{"location": "Tampa, FL"}\'')
    s.add_argument("--query", action="store_true", help="send the input as query parameters (GET tools)")
    s.set_defaults(fn=cmd_try)
    s = sub.add_parser("find-market")
    s.add_argument("query", nargs="+")
    s.set_defaults(fn=cmd_find_market)

    s = sub.add_parser("new")
    s.add_argument("name")
    s.add_argument("--build", action="store_true", help="build from the active listings in --market")
    s.add_argument("--market", action="append", help='a county, city or ZIP, e.g. "Broward County, FL" (repeatable)')
    s.add_argument("--enrich", metavar="FILE", help="your own CSV or Excel list")
    s.add_argument("--map", action="append", metavar="FIELD=COLUMN", help="override a detected column")
    s.add_argument("--addons", default="all", help=f"all, none, or a comma list of: {', '.join(ADDONS)}")
    s.add_argument("--min-listings", type=int, default=1, help="only agents with at least this many active listings")
    s.add_argument("--min-price", type=int)
    s.add_argument("--max-price", type=int)
    s.add_argument("--types", help=f"property types, comma list of: {', '.join(PROPERTY_TYPE_NAMES)}")
    s.add_argument("--producer-min", type=int, default=5, help="listings sold last year to count as a producer")
    s.add_argument("--phones-for", choices=["all", "emails", "producers"], default="all",
                   help="which agents get phone checks: everyone, those with a usable email, or producers")
    s.add_argument("--replace", action="store_true", help="overwrite an existing job of the same name")
    s.set_defaults(fn=cmd_new)

    for name, fn in (("quote", cmd_quote), ("status", cmd_status), ("export", cmd_export)):
        s = sub.add_parser(name)
        s.add_argument("name")
        s.set_defaults(fn=fn)
    s = sub.add_parser("add", help="add steps to a job, e.g. fix_phones after the phone check")
    s.add_argument("name")
    s.add_argument("addons", nargs="+", help=f"any of: {', '.join(ADDONS)}")
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("approve")
    s.add_argument("name")
    s.add_argument("--budget", type=float, required=True, help="most you're willing to spend, in dollars")
    s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("run")
    s.add_argument("name")
    s.add_argument("--only", choices=sorted(set(jobs.STEP_ORDER)), help="run just one step")
    s.add_argument("--limit", type=int, help="process at most this many per step (for a trial run)")
    s.set_defaults(fn=cmd_run)
    sub.add_parser("jobs").set_defaults(fn=cmd_jobs)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
