"""Email verification through Monid: AnyAPI's verifier by default, or
Hunter's (settings.MONID_EMAIL_TOOL = "hunter").

Scraped emails go stale: agents change brokerage and the old address
bounces, and bounces are what get a sending domain blacklisted. Only "ok"
results go in the ready-to-send export. "catch_all" means the domain
accepts everything, so the address can't be confirmed; it stays in the full
export, flagged, for whoever wants to take the risk.
"""
from __future__ import annotations

from leadkit import settings


class VerifyError(RuntimeError):
    pass


def configured() -> bool:
    return bool(settings.MONID_API_KEY)


def price() -> float:
    return settings.PRICES["hunter_email_verify" if settings.MONID_EMAIL_TOOL == "hunter" else "monid_email_verify"]


def anyapi_verdict(data: dict) -> str:
    """AnyAPI answers good / risky / bad, with a reason and catch-all and
    disposable flags. A "bad" because the mail server wouldn't say
    (smtp_unreachable) is unknown, not invalid: tested on Google-hosted
    domains it's what made-up addresses get, but a real inbox got it too."""
    status = str(data.get("status") or "").lower()
    if data.get("disposable") is True:
        return "disposable"
    if status == "good":
        return "ok"
    if status == "bad":
        return "unknown" if str(data.get("reason") or "").lower() == "smtp_unreachable" else "invalid"
    if data.get("catchAll") is True or data.get("catch_all") is True:
        return "catch_all"
    return "unknown"


def _anyapi(email: str) -> str:
    """Answers look like {"output": {"found": true, "data": {"items":
    [{"email", "status", "reason", "score", "catchAll", ...}]}}}."""
    from leadkit import monid
    try:
        res = monid.run("anyapi", "/email.verify", {"email": email}, timeout=120)
    except monid.MonidError as e:
        raise VerifyError(str(e)) from e
    data = res.get("output") or {}
    for key in ("data", "items"):
        if isinstance(data, dict) and key in data:
            data = data[key]
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict) or "status" not in data:
        raise RuntimeError(f"the email check for {email} came back empty")
    return anyapi_verdict(data)


# Hunter's verdicts, folded into the same words as AnyAPI's. Its
# `result` (deliverable / undeliverable / risky) is the plain answer; the
# `status` says why (accept_all is a catch-all domain).
def hunter_verdict(data: dict) -> str:
    result = (data.get("result") or "").lower()
    status = (data.get("status") or "").lower()
    if status == "disposable":
        return "disposable"
    if result == "deliverable" or status == "valid":
        return "ok"
    if result == "undeliverable" or status == "invalid":
        return "invalid"
    if status == "accept_all" or data.get("accept_all"):
        return "catch_all"
    return "unknown"


def _hunter(email: str) -> str:
    from leadkit import monid
    try:
        res = monid.run("hunterio", "/email-verifier", query={"email": email}, timeout=200)
    except monid.MonidError as e:
        raise VerifyError(str(e)) from e
    out = res.get("output") or {}
    data = out.get("data") if isinstance(out, dict) and isinstance(out.get("data"), dict) else out
    if not isinstance(data, dict) or not data:
        # Hunter couldn't reach the mail server (it bills nothing for that);
        # leave it for a later run rather than file it as unknown.
        raise RuntimeError(f"Hunter couldn't verify {email} this time")
    return hunter_verdict(data)


def verify(email: str) -> str:
    """ok, catch_all, unknown, invalid or disposable."""
    return _hunter(email) if settings.MONID_EMAIL_TOOL == "hunter" else _anyapi(email)
