"""Collier (FL): plain verified HTTPS, honest UA, custody never invented.

* No curl_cffi impersonation / browser UA; requests go through scrapers.plain_http.
* A bad certificate or 401/403/429 is raised (never retried / worked around).
* The UpdatePanel "pageRedirect -> errorpage.aspx" answer is a block, not an empty roster.
* The arrest report publishes no custody field, so Status is "" (unknown).
* MongoWriter never $sets a blank status over a stored one.
Synthetic data only; no network.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import requests

from core.models import ArrestRecord
from scrapers.counties.collier import CollierCountyScraper
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import EgressBlocked
from tests.test_staff_edits_survive_rescrape import FakeArrests, _writer

ROOT = Path(__file__).resolve().parents[1]
GET_HTML = ('<form><input id="__VIEWSTATE" value="vs"/>'
            '<input id="__VIEWSTATEGENERATOR" value="gen"/></form>')
ERROR_DELTA = "1|#||4|32|pageRedirect||%2farrestsearch%2ferrorpage.aspx|"


def _resp(status=200, body="", ctype="text/html"):
    r = requests.Response()
    r.status_code = status
    r._content = body.encode()
    r.headers["content-type"] = ctype
    return r


@pytest.fixture()
def fake_http(monkeypatch):
    monkeypatch.setattr("scrapers.counties.collier.time.sleep", lambda *_a: None)

    def _install(handler):
        calls = []

        def _fake(sess, method, url, **kw):
            calls.append((method, kw, dict(sess.headers)))
            return handler(method, url, kw)

        monkeypatch.setattr(requests.Session, "request", _fake)
        return calls

    return _install


def test_module_has_no_impersonation_or_hardcoded_custody():
    src = (ROOT / "scrapers" / "counties" / "collier.py").read_text()
    for banned in (r"curl_cffi", r"impersonate\s*=", r"Mozilla/5\.0", r"verify\s*=\s*False",
                   r'status\s*=\s*"In Custody"', r'Status\s*=\s*"In Custody"'):
        assert not re.search(banned, src), banned
    assert "from scrapers.plain_http import" in src


def test_requests_are_verified_and_honest(fake_http):
    calls = fake_http(lambda m, u, kw: _resp(200, GET_HTML) if m == "GET" else _resp(200, "<table></table>"))
    assert CollierCountyScraper().scrape() == []
    assert calls and all(kw["verify"] is True and "impersonate" not in kw for _m, kw, _h in calls)
    assert all(h["User-Agent"] == HONEST_USER_AGENT for _m, _kw, h in calls)


def test_error_redirect_is_a_block_not_empty(fake_http):
    fake_http(lambda m, u, kw: _resp(200, GET_HTML) if m == "GET" else _resp(200, ERROR_DELTA, "text/plain"))
    with pytest.raises(EgressBlocked, match=r"^egress_block: Collier"):
        CollierCountyScraper().scrape()


def test_bad_certificate_raises_once(fake_http):
    n = []

    def handler(*_a):
        n.append(1)
        raise requests.exceptions.SSLError("certificate verify failed")

    fake_http(handler)
    with pytest.raises(SourceTLSInvalid):
        CollierCountyScraper().scrape()
    assert len(n) == 1  # not retried


def test_403_raises_once(fake_http):
    n = []

    def handler(*_a):
        n.append(1)
        return _resp(403, "denied")

    fake_http(handler)
    with pytest.raises(EgressBlocked):
        CollierCountyScraper().scrape()
    assert len(n) == 1


def test_parsed_record_status_is_unknown():
    html = (
        "<table><tr><td>Name</td><td>Date of Birth</td><td>Residence</td>"
        "<td>SYNTH, ONE</td><td>01/01/1990</td><td>NAPLES, FL 34102</td></tr></table>"
        "<table><tr><td>Booking Number</td><td>202600000123</td>"
        "<td>Booking Date</td><td>10/14/2026</td></tr></table>"
    )
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    records = CollierCountyScraper()._parse_arrest_tables(soup.find_all("table"), soup)
    assert len(records) == 1
    assert records[0].Booking_Number == "202600000123"
    assert records[0].Status == ""


def test_writer_never_blanks_a_stored_status():
    stored = {"booking_number": "C1", "county": "Collier", "state": "FL", "status": "Released"}
    arrests = FakeArrests([stored])
    rec = ArrestRecord(Booking_Number="C1", County="Collier", State="FL", Full_Name="SYN, ONE", Status="")
    _writer(arrests).write_records([rec], "Collier")
    assert arrests.one("C1")["status"] == "Released"
    assert all("status" not in s for s in arrests.sets)


def test_writer_new_record_with_unknown_status_has_no_invented_status():
    arrests = FakeArrests([])
    rec = ArrestRecord(Booking_Number="C2", County="Collier", State="FL", Full_Name="SYN, TWO", Status="")
    _writer(arrests).write_records([rec], "Collier")
    assert arrests.one("C2").get("status", "") == ""


def test_writer_published_status_still_updates():
    arrests = FakeArrests([{"booking_number": "C3", "county": "Lee", "state": "FL", "status": "In Custody"}])
    rec = ArrestRecord(Booking_Number="C3", County="Lee", State="FL", Full_Name="SYN, THREE", Status="Released")
    _writer(arrests).write_records([rec], "Lee")
    assert arrests.one("C3")["status"] == "Released"
