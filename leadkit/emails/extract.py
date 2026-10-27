"""Extract and rank agent emails from fetched page HTML.

A page (brokerage profile, personal site, directory) often lists MANY emails —
the agent's plus colleagues, support, image filenames, tracking junk. We pull all
candidates (plain text, mailto: links, and lightly obfuscated forms), drop junk,
then score each by how well its local-part matches the target agent's name so we
pick THEIR email, not a colleague's.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote

# All quantifiers are BOUNDED ({m,n}, not + or *) so these can't catastrophically
# backtrack (ReDoS) on hostile page content — an unbounded version spun a CPU core.
EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]{1,64}@(?:[a-zA-Z0-9\-]{1,63}\.){1,5}[a-zA-Z]{2,24}')
MAILTO_RE = re.compile(r'mailto:([^"\'?>\s]{3,120})', re.I)
# Obfuscated: "john.doe [at] compass [dot] com". The at/dot separators must be
# BRACKETED or SPACE-delimited words (never letter-adjacent, so we don't match
# "at" inside "location"). Bounded lengths + limited whitespace keep it linear.
OBFUS_RE = re.compile(
    r'([a-z0-9._%+\-]{1,64})[ ]{0,3}(?:[\[\(\{][ ]{0,2}at[ ]{0,2}[\]\)\}]|[ ]{1,3}at[ ]{1,3})[ ]{0,3}'
    r'([a-z0-9.\-]{1,63})[ ]{0,3}(?:[\[\(\{][ ]{0,2}dot[ ]{0,2}[\]\)\}]|[ ]{1,3}dot[ ]{1,3})[ ]{0,3}([a-z]{2,24})',
    re.I)
# TLDs that are really code/file artifacts, not domains.
_BAD_TLD = {"href", "html", "htm", "php", "js", "jsx", "ts", "css", "json", "xml",
            "aspx", "asp", "png", "jpg", "jpeg", "gif", "svg", "webp", "map",
            "length", "value", "click", "top", "self", "min", "prototype"}

_JUNK_LOCAL = {"info", "support", "admin", "administration", "contact", "hello",
               "sales", "help", "noreply", "no-reply", "team", "office", "marketing",
               "webmaster", "privacy", "careers", "media", "press", "leads", "hi",
               "hello", "inquiries", "service", "customerservice", "reception",
               "dmca", "dmcanotice", "pr", "legal", "compliance", "feedback",
               "subscribe", "unsubscribe", "abuse", "postmaster", "listings", "mls"}
_JUNK_DOMAIN_SUBSTR = ("sentry", "wixpress", "example.com", "email.com", "domain.com",
                       "godaddy", "cloudflare", "schema.org", "w3.org", "googleapis",
                       "yourdomain", "sentry.io", "wix.com", "squarespace")
# Domains that are NEVER a personal agent email — MLS data feeds, referral
# platforms, portals, marketing tools. An address here is a system/role account.
_NOT_PERSONAL_DOMAINS = ("mlsgrid.com", "listhub.com", "retsly.com", "rets.ly",
                         "brightmls.com", "homelight.com", "opcity.com", "upnest.com",
                         "referralexchange.com", "realtor.com", "zillow.com",
                         "trulia.com", "mailchimp.com", "hubspot.com", "constantcontact.com",
                         "sentry.io", "wordpress.com", "squarespace.com")
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".css", ".js")
_FREE = ("gmail.com", "yahoo.com", "aol.com", "outlook.com", "hotmail.com",
         "icloud.com", "me.com", "comcast.net", "att.net", "bellsouth.net")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def _clean(email: str) -> str | None:
    e = email.strip().strip(".").lower()
    e = unquote(e)
    if "@" not in e:
        return None
    local, _, domain = e.partition("@")
    if any(e.endswith(ext) for ext in _IMG_EXT):
        return None
    if local in _JUNK_LOCAL:
        return None
    if any(j in domain for j in _JUNK_DOMAIN_SUBSTR):
        return None
    if any(domain == d or domain.endswith("." + d) for d in _NOT_PERSONAL_DOMAINS):
        return None
    if len(local) < 2 or "." not in domain or len(domain) < 4:
        return None
    if not re.fullmatch(r'[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}', e):
        return None
    tld = domain.rsplit(".", 1)[-1]
    if tld in _BAD_TLD or not (2 <= len(tld) <= 24):
        return None
    # local-parts that are really chained JS identifiers (a.b.c.d) — reject
    if local.count(".") >= 3:
        return None
    return e


def find_emails(html: str) -> list[str]:
    """All plausible emails on the page — plain text, mailto:, and de-obfuscated —
    junk removed, deduped (order-preserving, mailto first as highest-signal)."""
    # Cap input: emails live in the head/early body, and running the regexes over a
    # multi-MB page (minified JS, base64 blobs) can pin a CPU core for minutes.
    html = (html or "")[:600_000]
    out, seen = [], set()
    candidates = []
    candidates += MAILTO_RE.findall(html or "")                      # highest signal
    candidates += EMAIL_RE.findall(html or "")
    candidates += [f"{a}@{b}.{c}" for a, b, c in OBFUS_RE.findall(html or "")]
    for raw in candidates:
        e = _clean(raw)
        if e and e not in seen:
            seen.add(e)
            out.append(e)
    return out


def score_email(email: str, first: str, last: str) -> int:
    """How strongly an email's local-part matches an agent's name. Higher = better.

    e.g. ben.stafford@compass.com vs (Benjamin, Stafford): last name present (+3),
    first-initial present (+1). A colleague's email on the same page scores lower.
    """
    local = _norm(email.split("@")[0])
    f, la = _norm(first), _norm(last)
    if not local:
        return 0
    score = 0
    if la and la in local:
        score += 3
    if f and f in local:
        score += 2
    elif f and local.startswith(f[:1]):
        score += 1
    if score >= 3 and email.split("@")[1] in _FREE:
        score += 1
    return score


def best_email_for_agent(html: str, name: str, url: str = "",
                         page_is_agents: bool = False) -> dict | None:
    """Pick the best-matching agent email on a page. Returns {email, score} or None.

    Default requires a last-name match (min score 3) to avoid grabbing a
    colleague's email off a shared page. But when the page clearly belongs to THIS
    agent — its URL slug contains the agent's name, or the caller flags it as the
    agent's own profile — we relax to a first-name/initial match, and accept a lone
    email outright, catching branded/nickname addresses that omit the surname."""
    parts = name.split()
    if not parts:
        return None
    first, last = parts[0], parts[-1]
    emails = find_emails(html)
    if not emails:
        return None

    own_page = page_is_agents or (bool(url) and (
        _norm(first) in _norm(url) and _norm(last) in _norm(url)))

    threshold = 2 if own_page else 3
    best = None
    for email in emails:
        s = score_email(email, first, last)
        if s >= threshold and (best is None or s > best["score"]):
            best = {"email": email, "score": s}

    # On the agent's own page with exactly one real candidate, accept it even if it
    # carries no name signal (branded/team address the agent uses).
    if best is None and own_page and len(emails) == 1:
        best = {"email": emails[0], "score": 1}
    return best
