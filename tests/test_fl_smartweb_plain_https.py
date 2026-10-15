"""fl_smartweb: honest UA via plain_http, certificate checks on; blocks / bad certs fail visibly."""
import inspect

import pytest
import requests

from scrapers import fl_smartweb
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import EgressBlocked

COUNTIES = ["bradford", "dixie", "escambia", "gilchrist", "glades", "hamilton",
            "madison", "putnam", "santa_rosa", "sumter", "taylor"]


class _Resp:
    def __init__(self, code=200, text="<html>no form</html>"):
        self.status_code, self.text = code, text
        self.headers = {"content-type": "text/html"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.status_code)


def _install(monkeypatch, resp=None, exc=None):
    calls = []

    def fake(self, method, url, **kw):
        calls.append({"method": method, "kw": kw, "headers": dict(self.headers)})
        if exc:
            raise exc
        return resp

    monkeypatch.setattr(requests.Session, "request", fake)
    return calls


def _run():
    return fl_smartweb.scrape_smartweb_jail_view(
        county="Escambia", facility="X", base_url="https://example.test/smartwebclient", lookback_days=1)


def test_no_chrome_disguise_or_tls_off():
    src = inspect.getsource(fl_smartweb)
    for banned in ("Mozilla/", "Chrome/", "impersonate", "curl_cffi", "verify=False", "requests.Session()",
                   "session.get(", "session.post("):
        assert banned not in src, banned


def test_honest_ua_and_verify(monkeypatch):
    calls = _install(monkeypatch, _Resp())
    with pytest.raises(RuntimeError, match="search form changed"):
        _run()
    assert calls[0]["headers"]["User-Agent"] == HONEST_USER_AGENT
    assert calls[0]["kw"]["verify"] is True
    assert "Chrome" not in calls[0]["headers"]["User-Agent"]


@pytest.mark.parametrize("code", [401, 403, 429])
def test_block_fails_visibly(monkeypatch, code):
    _install(monkeypatch, _Resp(code=code, text=""))
    with pytest.raises(EgressBlocked, match=r"^egress_block: Escambia"):
        _run()


def test_bad_certificate_fails_visibly(monkeypatch):
    _install(monkeypatch, exc=requests.exceptions.SSLError("certificate verify failed"))
    with pytest.raises(SourceTLSInvalid, match=r"^tls_cert_invalid: Escambia"):
        _run()


def test_dropped_handshake_is_connection_reset(monkeypatch):
    import ssl
    try:
        raise requests.exceptions.SSLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol"))
    except requests.exceptions.SSLError as e:
        exc = e
    _install(monkeypatch, exc=exc)
    with pytest.raises(EgressBlocked) as ei:
        _run()
    assert "connection_reset" in str(ei.value) and not isinstance(ei.value, SourceTLSInvalid)


@pytest.mark.parametrize("mod", COUNTIES)
def test_county_modules_have_no_own_disguise(mod):
    import importlib
    src = inspect.getsource(importlib.import_module(f"scrapers.counties.{mod}"))
    for banned in ("Mozilla/", "Chrome/", "impersonate", "verify=False"):
        assert banned not in src, (mod, banned)
