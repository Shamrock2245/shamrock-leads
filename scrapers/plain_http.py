"""Plain, honest HTTPS for county sources.

One small wrapper over ``requests`` for counties that used curl_cffi Chrome
impersonation and ``verify=False``:

* honest User-Agent (``HONEST_USER_AGENT``), no browser fingerprint,
* certificate verification always on (``verify`` is never passed as False),
* a certificate that actually fails verification raises
  :class:`SourceTLSInvalid` (``tls_cert_invalid:``) instead of skipping
  verification; a handshake the far side drops or resets before any
  certificate is verified (unexpected EOF, reset) raises
  :class:`~scrapers.scraper_resilience.EgressBlocked` with
  ``egress_block: ... connection_reset`` -- it is not a certificate problem,
* a connection the far side aborts, resets or closes before any response
  (``http.client.RemoteDisconnected``, ``ConnectionResetError``,
  ``ConnectionAbortedError``, urllib3 ``ProtocolError('Connection aborted.')``,
  EOF before the status line) raises ``EgressBlocked`` with the same
  ``egress_block: <county> connection_reset`` label. Timeouts are NOT mapped:
  they propagate unchanged (``requests.Timeout``) and keep their own
  ``network``/timeout classification. DNS failures and refused connections
  also propagate unchanged. Nothing is retried, with weaker settings or otherwise,
* 401 / 403 / 429 or a challenge page raises
  :class:`~scrapers.scraper_resilience.EgressBlocked` (``egress_block:``).

Both are ``AntiBotBlocked`` subclasses, so the resilience layer never retries
them and Health records the run as blocked; nothing works around the block.
"""
from __future__ import annotations

import http.client
import ssl
from typing import Any

import requests
from socket import timeout as socket_timeout
from urllib3.exceptions import ProtocolError as Urllib3ProtocolError
from urllib3.exceptions import TimeoutError as Urllib3TimeoutError

from scrapers.scraper_resilience import AntiBotBlocked, EgressBlocked

HONEST_USER_AGENT = "ShamrockLeads/1.0 (+https://www.shamrockbailbonds.biz; public arrest records)"
BLOCK_STATUSES = frozenset({401, 403, 429})
_CHALLENGE_MARKERS = ("cf-chl", "challenge-platform", "attention required", "captcha", "access denied")


class SourceTLSInvalid(AntiBotBlocked):
    """The source's TLS certificate failed verification. Never retried, never bypassed."""


_CERT_FAIL_MARKERS = ("certificate verify failed", "certificate_verify_failed", "hostname mismatch",
                      "doesn't match", "self signed certificate", "self-signed certificate",
                      "certificate has expired", "unable to get local issuer certificate",
                      "unable to verify the first certificate", "certificate is not yet valid", "certificate revoked")


def _is_cert_verification_failure(exc: BaseException) -> bool:
    """True only when a certificate was presented and failed verification."""
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        e = stack.pop()
        if id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, ssl.SSLCertVerificationError):
            return True
        if any(m in str(e).lower() for m in _CERT_FAIL_MARKERS):
            return True
        for nxt in (e.__cause__, e.__context__, *[a for a in getattr(e, "args", ()) if isinstance(a, BaseException)],
                    getattr(e, "reason", None)):
            if isinstance(nxt, BaseException):
                stack.append(nxt)
    return False


_RESET_TYPES = (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError, ssl.SSLEOFError)
_RESET_MARKERS = ("connection aborted", "connection reset", "remote end closed connection without response",
                  "remotedisconnected", "unexpected eof", "eof occurred in violation of protocol",
                  "connection broken: incompleteread(0 bytes", "econnreset")


def _chain(exc: BaseException):
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        e = stack.pop()
        if id(e) in seen:
            continue
        seen.add(id(e))
        yield e
        for nxt in (e.__cause__, e.__context__, *[a for a in getattr(e, "args", ()) if isinstance(a, BaseException)],
                    getattr(e, "reason", None)):
            if isinstance(nxt, BaseException):
                stack.append(nxt)


def _is_timeout(exc: BaseException) -> bool:
    return any(isinstance(e, (requests.exceptions.Timeout, Urllib3TimeoutError, TimeoutError, socket_timeout)) for e in _chain(exc))


def _is_connection_reset(exc: BaseException) -> bool:
    """True when the far side aborted/reset/closed the connection before any response."""
    for e in _chain(exc):
        if isinstance(e, _RESET_TYPES):
            return True
        if any(m in str(e).lower() for m in _RESET_MARKERS):
            return True
    return False


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
        if _is_cert_verification_failure(exc):
            raise SourceTLSInvalid(f"tls_cert_invalid: {county} certificate failed verification ({type(exc).__name__})") from exc
        raise EgressBlocked(
            f"egress_block: {county} connection_reset: TLS handshake dropped before any certificate was verified ({type(exc).__name__})"
        ) from exc
    except (requests.exceptions.ConnectionError, Urllib3ProtocolError, http.client.HTTPException, ConnectionError) as exc:
        if _is_timeout(exc) or not _is_connection_reset(exc):
            raise  # timeouts, DNS, refused: unchanged, under their existing labels
        raise EgressBlocked(
            f"egress_block: {county} connection_reset: connection closed before any response ({type(exc).__name__})"
        ) from exc
    if resp.status_code in BLOCK_STATUSES:
        raise EgressBlocked(f"egress_block: {county} HTTP {resp.status_code}")
    ctype = (resp.headers.get("content-type") or "").lower()
    if "html" in ctype and resp.status_code >= 400:
        head = resp.text[:4000].lower()
        if any(m in head for m in _CHALLENGE_MARKERS):
            raise EgressBlocked(f"egress_block: {county} challenge page HTTP {resp.status_code}")
    return resp
