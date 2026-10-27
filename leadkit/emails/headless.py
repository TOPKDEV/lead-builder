"""A real Chrome window for the brokerage sites that block plain requests.

Many brokerage sites sit behind Cloudflare-style JavaScript challenges. Real
Chrome clears them, but only from a clean home connection: residential proxy
IPs are flagged by those firewalls. So this runs direct, on the user's own
computer, and never from a server.

Optional: `pip install patchright && patchright install chromium`. On a
Linux box with no screen, run under `xvfb-run -a`.
"""
from __future__ import annotations

import time

RECYCLE_EVERY = 30   # Chrome leaks memory across navigations; restart it this often
_CHALLENGE_TITLES = ("just a moment", "checking your browser", "attention required",
                     "verifying you are human", "verify you are human", "one moment", "please wait")
_BLOCKED = ("sorry, you have been blocked", "error 1020", "error 1010", "access denied")


def available() -> bool:
    try:
        import patchright  # noqa: F401
        return True
    except ImportError:
        return False


class _Page:
    def __init__(self, text: str):
        self.text = text


class HeadlessSession:
    """One browser per worker thread; drop-in for the email finder's fetch
    session, which only reads .get(url).text."""

    def __init__(self, profile_dir: str, challenge_wait: float = 16):
        from patchright.sync_api import sync_playwright
        self.profile_dir = profile_dir
        self.challenge_wait = challenge_wait
        self._pw = sync_playwright().start()
        self._open()
        self.visits = 0

    def _open(self):
        opts = dict(user_data_dir=self.profile_dir, headless=False, no_viewport=True,
                    locale="en-US")
        try:
            self._ctx = self._pw.chromium.launch_persistent_context(channel="chrome", **opts)
        except Exception:
            # No Google Chrome installed: patchright's own Chromium will do.
            self._ctx = self._pw.chromium.launch_persistent_context(**opts)
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    def get(self, url: str) -> _Page:
        self.visits += 1
        if self.visits % RECYCLE_EVERY == 0:
            self._ctx.close()
            self._open()
        self._page.goto(url, wait_until="domcontentloaded", timeout=22_000)
        deadline = time.monotonic() + self.challenge_wait
        while time.monotonic() < deadline:
            try:
                body = (self._page.content() or "").lower()
                title = (self._page.title() or "").lower()
            except Exception:
                body, title = "", ""
            if any(b in body for b in _BLOCKED):
                break
            if title and not any(t in title for t in _CHALLENGE_TITLES) and "cf-chl" not in body:
                break
            time.sleep(2)
        try:
            return _Page(self._page.content())
        except Exception:
            return _Page("")

    def close(self):
        for closer in (self._ctx.close, self._pw.stop):
            try:
                closer()
            except Exception:
                pass
