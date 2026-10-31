"""Whether the Monid key is in .env and actually works, and so which parts
of the tool are ready. Testing it costs one search (a tenth of a cent)."""
from __future__ import annotations

import sys

from leadkit import linetype, monid, settings
from leadkit.emails import headless


def _check(label: str, fn) -> tuple[bool, str]:
    try:
        return True, fn()
    except Exception as e:
        return False, str(e)[:160]


def _monid() -> str:
    found = monid.google("homes.com real estate agent Miami")
    return "works" if found else "answered, but the test search came back empty"


# (name, the .env keys it needs, test). Shared with the keys page.
CHECKS = [
    ("monid", ["MONID_API_KEY"], _monid),
    ("twilio", ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"], linetype.account_check),
]


def present() -> dict[str, bool]:
    """Which services have keys saved, without testing them (free)."""
    return {name: all(getattr(settings, k) for k in keys) for name, keys, _ in CHECKS}


def check_all(only=None) -> dict[str, tuple[str, str]]:
    """{name: (state, message)}, state one of ok, bad, missing. `only` limits
    the live tests to some services (the Monid test costs a search)."""
    out = {}
    for name, keys, fn in CHECKS:
        if only is not None and name not in only:
            continue
        if not all(getattr(settings, k) for k in keys):
            out[name] = ("missing", "not set")
            continue
        good, msg = _check(name, fn)
        out[name] = ("ok" if good else "bad", msg)
    return out


def ready(ok: dict[str, bool], chrome: bool) -> dict[str, bool]:
    m = ok.get("monid", False)
    return {
        "build a list from active listings": m,
        "find emails": m,
        "deep email pass (Chrome)": m and chrome,
        "verify emails": m,
        "check phones (cell vs landline)": m,
        "replace office numbers with the agent's cell": m,
        "homes.com sales volume": m,
    }


def run() -> dict[str, bool]:
    print(f"Python {sys.version.split()[0]}" + ("" if sys.version_info >= (3, 9) else "  <- needs 3.9 or newer"))
    if not settings.ENV_FILE.exists():
        print("No keys saved yet. Run: python leads.py keys")
    print("\nKeys")
    results = check_all()
    for name, (state, msg) in results.items():
        label = {"ok": "OK ", "bad": "BAD", "missing": "   "}[state]
        print(f"  {name:<16} {label} {msg}")
    chrome = headless.available()
    print(f"  {'chrome':<16} {'installed' if chrome else 'not installed (only needed for the deep email pass)'}")
    ok = {name: state == "ok" for name, (state, _) in results.items()}
    print("\nWhat you can run")
    for what, yes in ready(ok, chrome).items():
        print(f"  [{'x' if yes else ' '}] {what}")
    return ok
