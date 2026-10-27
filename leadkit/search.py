"""Web search through Monid (Google, via MrScraper), returning result URLs."""
from __future__ import annotations

from leadkit import monid, settings


class OutOfCredits(RuntimeError):
    """Raised instead of returning no results: an empty answer would mark
    every remaining agent "not found", a trap the first build fell into
    when its search credits ran out mid-run."""


def provider() -> str | None:
    return "monid" if settings.MONID_API_KEY else None


def search(query: str, usage=None) -> list[str]:
    if not settings.MONID_API_KEY:
        raise RuntimeError("No Monid key: run python leads.py keys")
    if usage:
        usage("monid")
    try:
        return monid.google(query)
    except monid.MonidError as e:
        raise OutOfCredits(str(e)) from e
