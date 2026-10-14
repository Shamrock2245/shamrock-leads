"""Polk / Volusia / Pasco (FL): plain requests, honest User-Agent, TLS verified.

No curl_cffi Chrome impersonation, no ``verify=False``. A bad certificate
raises SourceTLSInvalid (``tls_cert_invalid:``) and a 401/403/429 raises
EgressBlocked (``egress_block:``); neither is swallowed by per-row handlers.
Synthetic responses only; no network.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import requests

from scrapers import plain_http
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid, make_session, request
from scrapers.scraper_resilience import ERROR_ANTI_BOT, EgressBlocked, classify_exception

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("polk", "volusia", "pasco")


def _resp(status=200, body="", ctype="text/html"):
    r = requests.Response()
    r.status_code = status
    r._content = body.encode() if isinstance(body, str) else body
    r.headers["content-type"] = ctype
    return r


class _Recorder:
    def __init__(self, behaviour):
        self.calls = []
        self.behaviour = behaviour

    def __call__(self, sess, method, url, **kw):
        self.calls.append((method, url, kw, dict(sess.headers)))
        return self.behaviour(method, url, kw)


@pytest.fixture()
def patch_request(monkeypatch):
    def _install(behaviour):
        rec = _Recorder(behaviour)

        def _fake(sess, method, url, **kw):
            return rec(sess, method, url, **kw)

        monkeypatch.setattr(requests.Session, "request", _fake)
        return rec

    return _install


def _ssl(*_a):
    raise requests.exceptions.SSLError("certificate verify failed: unable to get local issuer certificate")


def _forbidden(*_a):
    return _resp(403, "<html>Access Denied</html>")


@pytest.mark.parametrize("name", MODULES)
def test_module_has_no_impersonation_or_insecure_tls(name):
    src = (ROOT / "scrapers" / "counties" / f"{name}.py").read_text()
    for banned in (r"curl_cffi", r"impersonate", r"IMPERSONATE", r"verify\s*=\s*False",
                   r"disable_warnings", r"Mozilla/5\.0"):
        assert not re.search(banned, src), (name, banned)
    assert "from scrapers.plain_http import" in src


def test_session_is_honest_and_verifies(patch_request):
    rec = patch_request(lambda *_a: _resp(200, "ok"))
    s = make_session()
    request(s, "GET", "https://example.invalid/", county="X", verify=False, impersonate="chrome131")
    method, _url, kw, headers = rec.calls[0]
    assert kw["verify"] is True and "impersonate" not in kw
    assert headers["User-Agent"] == HONEST_USER_AGENT
    assert "Mozilla" not in HONEST_USER_AGENT and "Chrome" not in HONEST_USER_AGENT


def test_bad_certificate_raises_tls_invalid_never_retried(patch_request):
    patch_request(_ssl)
    with pytest.raises(SourceTLSInvalid, match=r"^tls_cert_invalid: X"):
        request(make_session(), "GET", "https://example.invalid/", county="X")
    exc = SourceTLSInvalid("tls_cert_invalid: X")
    v = classify_exception(exc)
    assert v.error_class == ERROR_ANTI_BOT and not v.retryable


@pytest.mark.parametrize("status", [401, 403, 429])
def test_block_status_raises_egress_blocked(patch_request, status):
    patch_request(lambda *_a: _resp(status, "nope"))
    with pytest.raises(EgressBlocked, match=rf"^egress_block: X HTTP {status}"):
        request(make_session(), "GET", "https://example.invalid/", county="X")


def _scraper(name):
    from scrapers.counties.pasco import PascoCountyScraper
    from scrapers.counties.polk import PolkCountyScraper
    from scrapers.counties.volusia import VolusiaCountyScraper

    return {"polk": PolkCountyScraper, "volusia": VolusiaCountyScraper, "pasco": PascoCountyScraper}[name]()


@pytest.mark.parametrize("name", MODULES)
def test_scrape_bad_cert_fails_loudly(patch_request, monkeypatch, name):
    monkeypatch.setattr("time.sleep", lambda *_a: None)
    patch_request(_ssl)
    with pytest.raises(SourceTLSInvalid):
        _scraper(name).scrape()


@pytest.mark.parametrize("name", MODULES)
def test_scrape_block_fails_loudly(patch_request, monkeypatch, name):
    monkeypatch.setattr("time.sleep", lambda *_a: None)
    patch_request(_forbidden)
    with pytest.raises(EgressBlocked):
        _scraper(name).scrape()


def test_pasco_detail_block_is_not_swallowed(patch_request):
    from datetime import date

    roster = [{"firstName": "SYN", "middleName": "", "lastName": "THETIC", "suffix": "",
               "bookingNumber": "2026000001", "nameId": 1, "dob": "",
               "bookingDate": date.today().strftime("%m/%d/%Y"), "bookingTime": ""}]

    def behaviour(method, url, kw):
        if "GetInCustody" in url:
            return _resp(200, json.dumps(roster), "application/json")
        return _resp(403, "denied")

    patch_request(behaviour)
    with pytest.raises(EgressBlocked):
        _scraper("pasco").scrape()


def test_pasco_happy_path_uses_verified_plain_session(patch_request):
    rec = patch_request(lambda m, u, kw: _resp(200, "[]", "application/json"))
    assert _scraper("pasco").scrape() == []
    assert all(c[2]["verify"] is True for c in rec.calls)
    assert all(c[3]["User-Agent"] == plain_http.HONEST_USER_AGENT for c in rec.calls)
