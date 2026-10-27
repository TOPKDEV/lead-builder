"""Monid (monid.ai): one key in front of many scraping providers.

POST /v1/run with {provider, endpoint, input}. Fast endpoints answer on that
response; slower ones hand back a run id, which is polled until it reaches
a terminal status. Used here for Google search (MrScraper) and for
rendering homes.com profiles (Firecrawl with its enhanced proxy, which gets
through where a plain fetch is blocked).
"""
from __future__ import annotations

import time

import requests

from leadkit import settings

BASE = settings.env("MONID_API_URL", "https://api.monid.ai")
TERMINAL = {"COMPLETED", "FAILED", "BLOCKED", "STOPPED", "TIMED_OUT"}


class MonidError(RuntimeError):
    pass


def _headers() -> dict:
    if not settings.MONID_API_KEY:
        raise MonidError("MONID_API_KEY is not set in .env")
    return {"Authorization": f"Bearer {settings.MONID_API_KEY}", "Content-Type": "application/json"}


def run(provider: str, endpoint: str, body: dict | None = None, *, query: dict | None = None,
        timeout: float = 180, attempts: int = 2) -> dict:
    """One tool run. Monid takes the input split the way the tool's own API
    is (its `inspect` shows which): `body` for a POST body, `query` for
    query-string parameters. Sending one where the other belongs fails."""
    payload: dict = {}
    if body is not None:
        payload["body"] = body
    if query is not None:
        payload["queryParams"] = query
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(f"{BASE}/v1/run", headers=_headers(), timeout=timeout,
                                 json={"provider": provider, "endpoint": endpoint, "input": payload})
            if resp.status_code in (401, 403):
                raise MonidError(f"Monid rejected the key ({resp.status_code}). Check MONID_API_KEY.")
            if resp.status_code == 402:
                raise MonidError("Monid is out of credit (402). Add credit at monid.ai.")
            if resp.status_code == 429 or resp.status_code >= 500:
                raise RuntimeError(f"Monid answered {resp.status_code}")
            body = resp.json()
            if not body.get("runId") or body.get("status") in TERMINAL:
                return body
            deadline = time.monotonic() + timeout
            wait = 0.7
            while time.monotonic() < deadline:
                time.sleep(wait)
                wait = min(wait * 1.4, 5)
                poll = requests.get(f"{BASE}/v1/runs/{body['runId']}", headers=_headers(), timeout=30)
                if poll.status_code >= 500:
                    continue
                result = poll.json()
                if result.get("status") in TERMINAL:
                    return result
            raise RuntimeError(f"Monid run {body['runId']} did not finish in {timeout:.0f}s")
        except MonidError:
            raise
        except Exception as e:  # a blocked page or busy proxy usually clears on retry
            last = e
            if attempt < attempts:
                time.sleep(attempt)
    raise last or RuntimeError("Monid call failed")


def first_string(value, key: str) -> str | None:
    """The first string under `key` anywhere in a run's output."""
    if isinstance(value, list):
        for v in value:
            hit = first_string(v, key)
            if hit:
                return hit
    elif isinstance(value, dict):
        if isinstance(value.get(key), str):
            return value[key]
        for v in value.values():
            hit = first_string(v, key)
            if hit:
                return hit
    return None


def links(value) -> list[str]:
    """Every result URL in a search run's output, in order."""
    found: list[str] = []

    def walk(v):
        if isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for k in ("link", "url"):
                if isinstance(v.get(k), str) and v[k].startswith("http"):
                    found.append(v[k])
            for x in v.values():
                if isinstance(x, (list, dict)):
                    walk(x)

    walk(value.get("output", value) if isinstance(value, dict) else value)
    seen, out = set(), []
    for u in found:
        if u not in seen and "google." not in u:
            seen.add(u)
            out.append(u)
    return out


def google(query: str) -> list[str]:
    return links(run("mrscraper", "/serp/google", {"query": query}, timeout=90))


def render(url: str) -> str | None:
    """A page's markdown, rendered through Firecrawl's enhanced proxy."""
    res = run("firecrawl", "/scrape", {"url": url, "formats": ["markdown"],
                                       "proxy": "enhanced", "maxAge": 0}, timeout=150)
    return first_string(res, "markdown")

