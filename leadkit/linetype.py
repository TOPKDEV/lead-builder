"""A carrier's answer on a number: is it a cell, a landline or VoIP?

Optional, with a second key (Twilio's Line Type Intelligence). Monid's skip
trace says whose a number is; about a third of agents' numbers aren't on any
record, and this says what kind of line those are. It can't say whose, so a
cell it finds is listed apart, as "probably", and never replaces the skip
trace: on the Weston test, 4 of 9 numbers on someone else's record came back
"mobile".
"""
from __future__ import annotations

import requests

from leadkit import settings
from leadkit.phones import PhoneCheckError

URL = "https://lookups.twilio.com/v2/PhoneNumbers/"
_KINDS = {"mobile": "mobile", "landline": "landline", "fixedVoip": "voip", "nonFixedVoip": "voip",
          "tollFree": "toll-free"}


def configured() -> bool:
    return bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN)


def lookup(e164: str, usage=None) -> str:
    """mobile, landline, voip, toll-free or other. Raises PhoneCheckError on
    a rejected key or an empty balance, so a run stops and says so."""
    resp = requests.get(URL + e164, params={"Fields": "line_type_intelligence"}, timeout=30,
                        auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN))
    if resp.status_code in (401, 403):
        raise PhoneCheckError("Twilio rejected the key. Check it with: python leads.py keys")
    if resp.status_code == 402:
        raise PhoneCheckError("Twilio is out of credit. Add credit at twilio.com.")
    resp.raise_for_status()
    if usage:
        usage("twilio", 1, settings.PRICES["twilio_line"])
    line = resp.json().get("line_type_intelligence") or {}
    return _KINDS.get(line.get("type"), "other")


def account_check() -> str:
    """Whether the keys open the account (free)."""
    resp = requests.get(f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}.json",
                        auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN), timeout=30)
    if resp.status_code in (401, 403):
        raise RuntimeError("Twilio rejected these. Check the Account SID and Auth Token.")
    resp.raise_for_status()
    return "works"
