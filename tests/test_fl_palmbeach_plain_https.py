"""Palm Beach: plain honest HTTPS, cert verified, visible failure on blocks."""
import inspect
from unittest.mock import MagicMock

import pytest
import requests

from scrapers import plain_http
from scrapers.scraper_resilience import EgressBlocked
import scrapers.counties.palm_beach as pb
from scrapers.counties.palm_beach import PalmBeachCountyScraper, PalmBeachSourceChanged

FORM = """<html><body><form method="post" action="index.cfm">
<input type="hidden" name="tok" value="abc"><input id="start_date" name="start_date">
<input id="end_date" name="end_date"><input type="submit" id="process" value="Go"></form></body></html>"""
CARD = """<div id="allresults_1">Name: DOE, JOHN
Booking Number: 2026012709
Current Bond: $1,500.00
</div>"""
PAGE1 = f"<html><body>{CARD}<p>Page 1 of 2</p><a href='index.cfm?page=2'>2</a></body></html>"
PAGE2 = f"<html><body>{CARD.replace('2026012709','2026012710')}<p>Page 2 of 2</p></body></html>"


class FakeResp:
    def __init__(self, text, url="https://www3.pbso.org/blotter/index.cfm", status=200):
        self.text, self.url, self.status_code = text, url, status
        self.headers = {"content-type": "text/html"}


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls, self.headers, self.verify = list(responses), [], {}, True

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        nxt = self.responses.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


def _scrape_with(monkeypatch, responses):
    sess = FakeSession(responses)
    monkeypatch.setattr(plain_http, "make_session", lambda *a, **k: sess)
    monkeypatch.setattr(pb, "DAYS_BACK", 1)
    return PalmBeachCountyScraper().scrape(), sess


def test_source_has_no_browser_disguise_or_cert_bypass():
    src = inspect.getsource(pb)
    for bad in ("DrissionPage", "ChromiumPage", "Chrome/", "_get_browser_options", "verify=False",
                "ignore-certificate", "impersonate", "requests.get("):
        assert bad not in src.split('"""', 2)[2], bad


def test_session_is_honest_and_verified():
    s = plain_http.make_session()
    assert s.headers["User-Agent"] == plain_http.HONEST_USER_AGENT and s.verify is True


def test_form_submit_and_pagination(monkeypatch):
    recs, sess = _scrape_with(monkeypatch, [FakeResp(FORM), FakeResp(PAGE1), FakeResp(PAGE2)])
    assert [r.Booking_Number for r in recs] == ["2026012709", "2026012710"]
    assert recs[0].Bond_Amount == "1500.00"
    method, url, kw = sess.calls[1]
    assert method == "POST" and url.endswith("/blotter/index.cfm")
    assert kw["data"]["tok"] == "abc" and kw["data"]["start_date"] == kw["data"]["end_date"]
    assert "process" not in kw["data"]
    assert sess.calls[2][1].endswith("index.cfm?page=2")
    assert all(k.get("verify", True) is True for _, _, k in sess.calls)


def test_hcaptcha_page_is_egress_block(monkeypatch):
    page = "<html><iframe src='https://hcaptcha.com/x'></iframe></html>"
    with pytest.raises(EgressBlocked, match="egress_block: Palm Beach challenge"):
        _scrape_with(monkeypatch, [FakeResp(page)])


def test_http_403_is_egress_block(monkeypatch):
    with pytest.raises(EgressBlocked, match="egress_block: Palm Beach HTTP 403"):
        _scrape_with(monkeypatch, [FakeResp("no", status=403)])


def test_dropped_handshake_is_connection_reset(monkeypatch):
    with pytest.raises(EgressBlocked, match="connection_reset"):
        _scrape_with(monkeypatch, [requests.exceptions.SSLError("EOF occurred in violation of protocol")])


def test_bad_certificate_is_tls_cert_invalid(monkeypatch):
    with pytest.raises(plain_http.SourceTLSInvalid, match="tls_cert_invalid"):
        _scrape_with(monkeypatch, [requests.exceptions.SSLError("certificate verify failed")])


def test_missing_form_is_visible(monkeypatch):
    with pytest.raises(PalmBeachSourceChanged):
        _scrape_with(monkeypatch, [FakeResp("<html>moved</html>")])


def test_single_booking_block_raises_not_none(monkeypatch):
    sess = FakeSession([FakeResp("no", status=403)])
    monkeypatch.setattr(plain_http, "make_session", lambda *a, **k: sess)
    with pytest.raises(EgressBlocked):
        PalmBeachCountyScraper()._fetch_single_booking(
            "2026012709", "https://www3.pbso.org/blotter/index.cfm?booking=2026012709")
