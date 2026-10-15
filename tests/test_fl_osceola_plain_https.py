"""Osceola (FL): plain requests, honest User-Agent, TLS verified.

No curl_cffi impersonation, browser User-Agent or verify=False. A bad
certificate (the legacy-apps host omits its intermediate) raises
SourceTLSInvalid and a block raises EgressBlocked; neither is swallowed by the
per-day handler. Synthetic CSV only; no network.
"""
from __future__ import annotations

import inspect

import pytest
import requests

import scrapers.counties.osceola as osc
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import ERROR_ANTI_BOT, EgressBlocked, classify_exception

CSV = (
    "ARREST_NUMBER,FIRST_NAME,LAST_NAME,MIDDLE_NAME,ARREST_DATE,BIRTH_DATE,CITY,ARRESTING_AGENCY,STATUTE_LIST\n"
    "20261234,AAA,BBB,,10/14/2026,1/2/1990,KISSIMMEE,OCSO,784.03 BATTERY\n"
    "20261234,AAA,BBB,,10/14/2026,1/2/1990,KISSIMMEE,OCSO,843.02 RESIST\n"
    "X12,CCC,DDD,,10/14/2026,1/2/1990,KISSIMMEE,OCSO,812.014 THEFT\n"
)


def _resp(status=200, body="", ctype="text/csv"):
    r = requests.Response()
    r.status_code = status
    r._content = body.encode()
    r.headers["content-type"] = ctype
    return r


@pytest.fixture()
def fake(monkeypatch):
    calls = []

    def install(handler):
        def _req(sess, method, url, **kw):
            calls.append({"url": url, "kw": kw, "headers": dict(sess.headers)})
            return handler(method, url, kw)

        monkeypatch.setattr(requests.Session, "request", _req)
        return calls

    return install


def test_module_has_no_impersonation_or_tls_off():
    src = inspect.getsource(osc).split('"""', 2)[2]
    for banned in ("curl_cffi", "impersonate", "IMPERSONATE", "Mozilla/", "Chrome/", "verify=False"):
        assert banned not in src, banned


def test_requests_are_honest_and_verified_and_parse_unchanged(fake):
    calls = fake(lambda m, u, kw: _resp(200, CSV))
    recs = osc.OsceolaCountyScraper().scrape()
    assert len(calls) == osc.DAYS_BACK
    for c in calls:
        assert c["headers"]["User-Agent"] == HONEST_USER_AGENT
        assert c["kw"].get("verify") is True
        assert "impersonate" not in c["kw"]
    assert len(recs) == 1  # grouped by ARREST_NUMBER; malformed key skipped
    r = recs[0]
    assert r.Booking_Number == "20261234"
    assert "784.03 BATTERY" in r.Charges and "843.02 RESIST" in r.Charges
    assert r.Bond_Amount == ""  # the CSV publishes no bond; nothing invented


def test_bad_certificate_raises_tls_invalid(fake):
    def h(*_a):
        raise requests.exceptions.SSLError("unable to verify the first certificate")

    fake(h)
    with pytest.raises(SourceTLSInvalid) as ei:
        osc.OsceolaCountyScraper().scrape()
    assert str(ei.value).startswith("tls_cert_invalid:")
    assert classify_exception(ei.value).error_class == ERROR_ANTI_BOT


@pytest.mark.parametrize("code", [401, 403, 429])
def test_block_raises_and_is_not_swallowed(fake, code):
    fake(lambda m, u, kw: _resp(code, "denied", "text/html"))
    with pytest.raises(EgressBlocked) as ei:
        osc.OsceolaCountyScraper().scrape()
    assert str(ei.value).startswith("egress_block:")


def test_missing_day_is_skipped_not_raised(fake):
    fake(lambda m, u, kw: _resp(404, "", "text/html"))
    assert osc.OsceolaCountyScraper().scrape() == []


def test_blank_cells_do_not_drop_the_day(fake):
    # Pre-existing bug: a blank MIDDLE_NAME/CITY became NaN and .strip() raised,
    # so the per-day handler dropped every row for that day.
    fake(lambda m, u, kw: _resp(200, CSV))
    recs = osc.OsceolaCountyScraper().scrape()
    assert len(recs) == 1
    assert recs[0].Middle_Name == ""
