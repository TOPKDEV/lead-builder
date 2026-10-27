"""Find an agent's real, published email.

Per agent: two searches (brokerage-anchored, then a generic realtor-contact
angle), fetch the best few result pages, and on the agent's own brokerage or
personal site follow one level of contact/about/team links, where emails
usually live. Only an email actually printed on a page is returned, matched
to the agent's name so a colleague's address on a shared page is not taken.
No pattern guessing.

Aggregators (Zillow, Realtor.com, Homes.com) show a contact form, not an
email, so they are fetched last and Zillow and socials not at all.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from leadkit.emails.extract import best_email_for_agent
from leadkit.search import search

MAX_PAGES = 3
MAX_SUBPAGES = 2
_PORTAL = ("realtor.com", "homes.com", "redfin.com", "har.com", "movoto.com")
_SKIP = ("instagram.com", "facebook.com", "twitter.com", "x.com", "youtube.com", "tiktok.com",
         "pinterest.com", "reddit.com", "linkedin.com", "yelp.com", "zillow.com", "trulia.com")
_SUBPAGE_HINTS = ("contact", "about", "agent", "team", "meet", "our-team", "staff",
                  "profile", "bio", "realtor")

# Firms whose agents are on one corporate email domain: their own site is
# the first place to look.
CORPORATE_DOMAINS = {
    "compass": "compass.com", "douglas elliman": "elliman.com", "elliman": "elliman.com",
    "exp realty": "exprealty.com", "serhant": "serhant.com", "corcoran": "corcoran.com",
    "the agency": "theagencyre.com", "one sotheby": "onesothebysrealty.com",
    "sotheby": "sothebysrealty.com",
}


def corporate_domain(brokerage: str | None) -> str | None:
    b = (brokerage or "").lower()
    return next((d for k, d in CORPORATE_DOMAINS.items() if k in b), None)


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")


def queries(name: str, brokerage: str, city: str) -> list[str]:
    return [f'"{name}" {brokerage} {city} email'.replace("  ", " "),
            f'"{name}" realtor {city} contact email'.replace("  ", " ")]


def rank_urls(urls: list[str], corp: str | None, name: str) -> list[str]:
    """Brokerage's own domain, then pages slugged for this agent, then other
    sites, portals last. Socials dropped."""
    last = name.split()[-1].lower() if name.split() else ""

    def key(u: str) -> int:
        h = _host(u)
        if corp and corp in h:
            return 0
        if last and last in u.lower():
            return 1
        if any(p in h for p in _PORTAL):
            return 3
        return 2

    return sorted((u for u in urls if not any(s in _host(u) for s in _SKIP)), key=key)


def subpages(html: str, base_url: str, name: str) -> list[str]:
    base = _host(base_url)
    last = name.split()[-1].lower() if name.split() else ""
    hits: list[str] = []
    seen: set[str] = set()
    for href in re.findall(r'href=["\']([^"\'#]{1,500})["\']', html or ""):
        url = urljoin(base_url, href)
        if _host(url) != base or url in seen:
            continue
        low = url.lower()
        if last and last in low:
            seen.add(url)
            hits.insert(0, url)
        elif any(h in low for h in _SUBPAGE_HINTS):
            seen.add(url)
            hits.append(url)
    return hits[:MAX_SUBPAGES]


def _fetch(session, url: str) -> str:
    try:
        # Emails sit early in a page, and bounding the input keeps every regex
        # downstream fast on pathological pages.
        return session.get(url).text[:800_000]
    except Exception:
        return ""


def find_email(name: str, brokerage: str, city: str, fetch_session, usage=None,
               max_pages: int = MAX_PAGES, max_subpages: int = MAX_SUBPAGES) -> dict | None:
    """{email, source} or None. Raises search.OutOfCredits rather than
    reporting a miss it did not actually search for."""
    urls: list[str] = []
    for q in queries(name, brokerage or "", city or ""):
        for u in search(q, usage=usage):
            if u not in urls:
                urls.append(u)
        if len(urls) >= 12:
            break

    corp = corporate_domain(brokerage)
    for url in rank_urls(urls, corp, name)[:max_pages]:
        html = _fetch(fetch_session, url)
        if not html:
            continue
        hit = best_email_for_agent(html, name, url=url)
        if hit:
            return {"email": hit["email"], "source": url}
        host = _host(url)
        own_site = (corp and corp in host) or not any(p in host for p in _PORTAL)
        if own_site and max_subpages:
            for sub in subpages(html, url, name)[:max_subpages]:
                sub_html = _fetch(fetch_session, sub)
                hit = best_email_for_agent(sub_html, name, url=sub) if sub_html else None
                if hit:
                    return {"email": hit["email"], "source": sub}
    return None
