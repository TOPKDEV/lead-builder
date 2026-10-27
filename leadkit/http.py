"""The HTTP session for the thousands of brokerage sites the email finder
and the cell finder visit, straight from this computer."""
from __future__ import annotations

import logging
import random

import requests

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# Full browser-like headers: brokerage and portal sites reject bare
# requests-library headers far more often.
BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


class Session:
    """A browser-like session that fails fast: short timeout, one retry, no
    backoff, so a dead brokerage site can't stall a worker."""

    def __init__(self):
        self.http = requests.Session()
        self.http.headers.update(BASE_HEADERS)
        self.http.headers["User-Agent"] = random.choice(USER_AGENTS)

    def get(self, url: str, **kwargs) -> requests.Response:
        self.http.headers["User-Agent"] = random.choice(USER_AGENTS)
        for _ in range(2):
            try:
                resp = self.http.get(url, timeout=7, **kwargs)
            except (requests.Timeout, requests.ConnectionError):
                continue
            if resp.status_code == 200:
                return resp
            if resp.status_code not in (202, 403, 429):
                break
        raise RuntimeError(f"light get failed: {url}")
