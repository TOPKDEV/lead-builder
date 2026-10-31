"""Paths, keys and prices, all read from .env so nobody edits code to configure it."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
load_dotenv(ENV_FILE)

JOBS_DIR = ROOT / "jobs"
EXPORTS_DIR = ROOT / "exports"


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def env_float(name: str, default: float) -> float:
    try:
        return float(env(name) or default)
    except ValueError:
        return default


# --- Keys -------------------------------------------------------------------
# One key does everything: Monid (monid.ai) runs every outside tool the
# lead builder uses and bills them all to one balance. Twilio is optional:
# with it, numbers no record knows get a carrier's answer (leadkit/linetype.py).
KEY_NAMES = ["MONID_API_KEY", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"]
MONID_API_KEY = TWILIO_ACCOUNT_SID = TWILIO_AUTH_TOKEN = ""


def reload_keys() -> None:
    """Re-read the key, after the keys page has saved a new one."""
    load_dotenv(ENV_FILE, override=True)
    for name in KEY_NAMES:
        globals()[name] = env(name)


reload_keys()

# --- Prices, in dollars, used for quotes and the spend guard ------------------
# Monid's list prices as of October 2026. Anyone on a different plan can
# override them in .env, and the quote follows.
PRICES = {
    # Per run: a Google search (MrScraper) or one Firecrawl page render.
    "monid_call": env_float("PRICE_MONID_CALL", 0.0012),
    # Zillow search (AnyAPI), per call whatever it returns: counts the listings
    # in a price band before any are bought.
    "zillow_search": env_float("PRICE_ZILLOW_SEARCH", 0.0008),
    # Realtor.com search (AnyAPI): a fee per call plus one per listing
    # returned, each with its agent's name, phone, email and brokerage.
    "realtor_call": env_float("PRICE_REALTOR_CALL", 0.0055),
    "realtor_listing": env_float("PRICE_REALTOR_LISTING", 0.00165),
    # Email checks: AnyAPI $0.0066 per address; Hunter $0.01225 per definite verdict.
    "monid_email_verify": env_float("PRICE_MONID_EMAIL_VERIFY", 0.0066),
    "hunter_email_verify": env_float("PRICE_HUNTER_EMAIL_VERIFY", 0.01225),
    # AnyAPI skip trace, per person found (nobody found is free). It's the
    # phone check: traced by the number, it says whose it is and whether it's
    # a cell. Traced by email or name, it finds the agent's real cell.
    "skip_trace": env_float("PRICE_SKIP_TRACE", 0.0077),
    # Finding a cell the skip trace doesn't have: an agent's Zillow profile,
    # and DataLegion's people database, per call.
    "zillow_profile": env_float("PRICE_ZILLOW_PROFILE", 0.01),
    "people_lookup": env_float("PRICE_PEOPLE_LOOKUP", 0.065),
    # Twilio Line Type Intelligence, per number (the optional second key).
    "twilio_line": env_float("PRICE_TWILIO_LINE", 0.008),
}

# Which Monid tool checks emails: "anyapi" or "hunter".
MONID_EMAIL_TOOL = env("MONID_EMAIL_TOOL", "anyapi")

# Measured on the South Florida run: listings per unique agent.
LISTINGS_PER_AGENT = 2.15
# Share of agents a static + deep email pass ends up finding an email for.
EMAIL_HIT_RATE = 0.5

# --- Pacing -----------------------------------------------------------------
LISTING_WORKERS = int(env_float("LISTING_WORKERS", 8))
# Zillow counts cost a flat $0.0008 and mostly wait on the network, so more
# at once costs nothing extra and makes a county's quote come back sooner.
COUNT_WORKERS = int(env_float("COUNT_WORKERS", 16))
EMAIL_WORKERS = int(env_float("EMAIL_WORKERS", 8))
HEADLESS_WORKERS = int(env_float("HEADLESS_WORKERS", 3))  # 3 Chromes fit in 16 GB
LOOKUP_WORKERS = int(env_float("LOOKUP_WORKERS", 8))

# A run stops itself once actual spend passes the approved budget by this much.
BUDGET_OVERRUN = 1.25
