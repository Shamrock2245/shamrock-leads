"""Plain, honest HTTPS for county sources.

One small wrapper over ``requests`` for counties that used curl_cffi Chrome
impersonation and ``verify=False``:

* honest User-Agent (``HONEST_USER_AGENT``), no browser fingerprint,
* certificate verification always on (``verify`` is never passed as False),
* a bad certificate raises :class:`SourceTLSInvalid` (message starts with
  ``tls_cert_invalid:``) instead of skipping verification,
* 401 / 403 / 429 or a challenge page raises
  :class:`~scrapers.scraper_resilience.EgressBlocked` (``egress_block:``).

Both are ``AntiBotBlocked`` subclasses, so the resilience layer never retries
them and Health records the run as blocked; nothing works around the block.
"""
from __future__ import annotations

from typing import Any

import requests

from scrapers.scraper_resilience import AntiBotBlocked, EgressBlocked

HONEST_USER_AGENT = "ShamrockLeads/1.0 (+https://www.shamrockbailbonds.biz; public arrest records)"
BLOCK_STATUSES = frozenset({401, 403, 429})
_CHALLENGE_MARKERS = ("cf-chl", "challenge-platform", "attention required", "captcha", "access denied")


class SourceTLSInvalid(AntiBotBlocked):
    """The source's TLS certificate failed verification. Never retried, never bypassed."""


def make_session(extra_headers: dict | None = None) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": HONEST_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    if extra_headers:
        s.headers.update(extra_headers)
    s.verify = True
    return s


def request(session: requests.Session, method: str, url: str, *, county: str, **kwargs: Any) -> requests.Response:
    """Send one verified request; raise typed errors for bad TLS or a block."""
    kwargs.pop("verify", None)
    kwargs.pop("impersonate", None)
    kwargs.setdefault("timeout", 30)
    try:
        resp = session.request(method, url, verify=True, **kwargs)
    except requests.exceptions.SSLError as exc:
        raise SourceTLSInvalid(f"tls_cert_invalid: {county} certificate failed verification ({type(exc).__name__})") from exc
    if resp.status_code in BLOCK_STATUSES:
        raise EgressBlocked(f"egress_block: {county} HTTP {resp.status_code}")
    ctype = (resp.headers.get("content-type") or "").lower()
    if "html" in ctype and resp.status_code >= 400:
        head = resp.text[:4000].lower()
        if any(m in head for m in _CHALLENGE_MARKERS):
            raise EgressBlocked(f"egress_block: {county} challenge page HTTP {resp.status_code}")
    return resp
