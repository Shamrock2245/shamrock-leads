"""Seminole (FL): plain requests, honest User-Agent, TLS verified.

No curl_cffi impersonation or browser User-Agent. A block (401/403/429 or a
challenge) raises EgressBlocked and a bad certificate raises SourceTLSInvalid;
neither is swallowed per letter or per detail. Status comes only from the
detail page; bond is the published Total Bond or "". Synthetic data, no network.
"""
from __future__ import annotations

import inspect
import json

import pytest
import requests

import scrapers.counties.seminole as sem
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import ERROR_ANTI_BOT, EgressBlocked, classify_exception

DETAIL = """<html><body>
<div class="bookingHeader">Booking - {bk}</div>
<div><span>Booking Date</span><span>{bd}</span></div>
{status}
<div><span>Total Bond</span><span>{bond}</span></div>
<div class="chargeRow">BATTERY (M1)</div>
</body></html>"""


def _resp(status=200, body="", ctype="text/html"):
    r = requests.Response()
    r.status_code = status
    r._content = body.encode()
    r.headers["content-type"] = ctype
    r.url = "https://seminole.northpointesuite.com/custodyportal/"
    return r


def _roster(letter_rows):
    return json.dumps({"searchData": {"resultArray": letter_rows, "resultCount": len(letter_rows)}})


@pytest.fixture()
def fake(monkeypatch):
    calls = []

    def install(handler):
        def _req(sess, method, url, **kw):
            calls.append({"method": method, "url": url, "kw": kw, "headers": dict(sess.headers)})
            return handler(method, url, kw)

        monkeypatch.setattr(requests.Session, "request", _req)
        monkeypatch.setattr(sem.time, "sleep", lambda *_: None)
        return calls

    return install


def _today():
    from datetime import datetime

    return datetime.now().strftime("%m/%d/%Y")


def _handler(detail_html, roster_status=200):
    row = {"personId": 7, "firstName": "A", "lastName": "B"}

    def h(method, url, kw):
        if "DoSearch" in url:
            if roster_status != 200:
                return _resp(roster_status, "denied")
            letter = json.loads(kw["params"]["searchParams"])["LastName"]
            return _resp(200, _roster([row] if letter == "B" else []), "application/json")
        if "Details" in url:
            return _resp(200, detail_html)
        return _resp(200, "<html>portal</html>")

    return h


def test_module_has_no_impersonation_or_browser_ua():
    src = inspect.getsource(sem).split('"""', 2)[2]
    for banned in ("curl_cffi", "impersonate", "IMPERSONATE", "Mozilla/", "Chrome/", "verify=False"):
        assert banned not in src, banned


def test_every_request_is_honest_and_verified(fake):
    calls = fake(_handler(DETAIL.format(bk="2026001234", bd=_today(), status="<div><span>Status</span><span>IN JAIL</span></div>", bond="$1,500.00")))
    recs = sem.SeminoleCountyScraper().scrape()
    assert calls
    for c in calls:
        assert c["headers"]["User-Agent"] == HONEST_USER_AGENT
        assert c["kw"].get("verify") is True
        assert "impersonate" not in c["kw"]
        assert "User-Agent" not in (c["kw"].get("headers") or {})
    assert len(recs) == 1
    r = recs[0]
    assert r.Booking_Number == "2026001234"
    assert r.Status == "IN JAIL"
    assert r.Bond_Amount == "1500"


@pytest.mark.parametrize("bond,expected", [
    ("", ""), ("NO BOND", ""), ("HOLD", ""), ("N/A", ""), ("See Judge", ""),
    ("$0.00", "0"), ("$2,500.50", "2500.50"), ("1000", "1000"),
])
def test_bond_is_published_or_blank(bond, expected):
    assert sem.SeminoleCountyScraper._parse_bond(bond) == expected


def test_missing_status_and_bond_stay_blank(fake):
    fake(_handler(DETAIL.format(bk="2026005555", bd=_today(), status="", bond="")))
    recs = sem.SeminoleCountyScraper().scrape()
    assert len(recs) == 1
    assert recs[0].Status == ""
    assert recs[0].Bond_Amount == ""


def test_no_booking_header_means_no_record(fake):
    fake(_handler("<html><body>no header</body></html>"))
    assert sem.SeminoleCountyScraper().scrape() == []


@pytest.mark.parametrize("code", [401, 403, 429])
def test_block_on_portal_raises(fake, code):
    fake(lambda m, u, kw: _resp(code, "denied"))
    with pytest.raises(EgressBlocked) as ei:
        sem.SeminoleCountyScraper().scrape()
    assert str(ei.value).startswith("egress_block:")
    c = classify_exception(ei.value)
    assert c.error_class == ERROR_ANTI_BOT and c.retryable is False and c.egress_block is True


def test_block_on_dosearch_is_not_swallowed(fake):
    fake(_handler("", roster_status=403))
    with pytest.raises(EgressBlocked):
        sem.SeminoleCountyScraper().scrape()


def test_block_on_detail_is_not_swallowed(fake):
    base = _handler("")

    def h(m, u, kw):
        if "Details" in u:
            return _resp(429, "slow down")
        return base(m, u, kw)

    fake(h)
    with pytest.raises(EgressBlocked):
        sem.SeminoleCountyScraper().scrape()


def test_bad_certificate_raises_tls_invalid(fake):
    def h(*_a):
        raise requests.exceptions.SSLError("certificate verify failed")

    fake(h)
    with pytest.raises(SourceTLSInvalid) as ei:
        sem.SeminoleCountyScraper().scrape()
    assert str(ei.value).startswith("tls_cert_invalid:")


# ── 2026-10-15 (supersedes #208): status_source, empty charges, no names in logs ──

def test_published_status_records_status_source(fake):
    fake(_handler(DETAIL.format(bk="2026001111", bd=_today(), status="<div><span>Status</span><span>IN JAIL</span></div>", bond="$0.00")))
    r = sem.SeminoleCountyScraper().scrape()[0]
    assert r.Status == "IN JAIL"
    assert r.extra_data["status_source"] == "published_detail_status"
    assert r.Bond_Amount == "0"


def test_absent_status_has_no_status_source(fake):
    fake(_handler(DETAIL.format(bk="2026002222", bd=_today(), status="", bond="")))
    r = sem.SeminoleCountyScraper().scrape()[0]
    assert r.Status == "" and "status_source" not in (r.extra_data or {})


def test_no_charge_rows_means_empty_charges(fake):
    html = DETAIL.format(bk="2026003333", bd=_today(), status="", bond="").replace('<div class="chargeRow">BATTERY (M1)</div>', "")
    fake(_handler(html))
    assert sem.SeminoleCountyScraper().scrape()[0].Charges == ""


def test_detail_error_log_has_no_name(fake, caplog):
    def h(method, url, kw):
        if "Details" in url:
            raise ValueError("boom")
        return _handler("")(method, url, kw)

    fake(h)
    import logging
    with caplog.at_level(logging.DEBUG, logger=sem.logger.name):
        assert sem.SeminoleCountyScraper().scrape() == []
    assert any("detail error" in r.getMessage() for r in caplog.records)
    assert not any(" for B" in r.getMessage() for r in caplog.records)


def test_no_in_custody_literal_in_code():
    import inspect
    src = inspect.getsource(sem.SeminoleCountyScraper)
    assert 'or "In Custody"' not in src and 'or "0"' not in src
