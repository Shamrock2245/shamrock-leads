"""Duval (FL): honest plain HTTPS, typed errors, fail closed (docs/recon/FL_DUVAL_HONEST.md). Synthetic data."""
from __future__ import annotations

import inspect

import pytest
import requests

from dashboard.extensions import SCRAPER_SOURCE_STATES
from scrapers import plain_http
import scrapers.counties.duval as duval
from scrapers.counties.duval import DuvalCountyScraper, DuvalHTTPError
from scrapers.scraper_resilience import (
    ERROR_NETWORK, ERROR_PARSE_DRIFT, ERROR_URL_CHANGED, EgressBlocked, ParseDriftError, classify_exception,
)


class _Resp:
    def __init__(self, text="", status=200):
        self.text, self.status_code, self.url = text, status, duval.BASE_URL
        self.headers = {"content-type": "text/html"}


class _Session:
    def __init__(self, nxt):
        self.nxt, self.calls, self.headers, self.verify = nxt, [], {}, True

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if isinstance(self.nxt, BaseException):
            raise self.nxt
        return self.nxt


def _run(nxt):
    return DuvalCountyScraper()._scrape_with_session(_Session(nxt))


def _s():
    return DuvalCountyScraper.__new__(DuvalCountyScraper)


# ── identity / fail closed ──────────────────────────────────────────────────
def test_no_browser_disguise_or_cert_bypass_in_source():
    body = inspect.getsource(duval).split('"""', 2)[2]
    for bad in ("DrissionPage", "ChromiumPage", "Chrome/", "_get_browser_options", "verify=False",
                "impersonate", "curl_cffi", "requests.get(", "except Exception"):
        assert bad not in body, bad


def test_fail_closed_everywhere_and_no_request(monkeypatch):
    assert DuvalCountyScraper.SOURCE_CONTRACT_VALIDATED is False
    assert "tls_cert_invalid" in DuvalCountyScraper.SOURCE_CONTRACT_REASON
    assert "FL_DUVAL_HONEST.md" in DuvalCountyScraper.SOURCE_CONTRACT_REASON
    assert SCRAPER_SOURCE_STATES.get("Duval (FL)") == "fail_closed"
    monkeypatch.setattr(plain_http, "make_session", lambda *a, **k: pytest.fail("no source request while closed"))
    assert DuvalCountyScraper().scrape() == []


def test_session_is_honest_and_verified():
    s = plain_http.make_session()
    assert s.headers["User-Agent"] == plain_http.HONEST_USER_AGENT and s.verify is True


# ── typed errors on the reopen path ─────────────────────────────────────────
def test_bad_certificate_is_tls_cert_invalid():
    with pytest.raises(plain_http.SourceTLSInvalid, match="tls_cert_invalid"):
        _run(requests.exceptions.SSLError("certificate verify failed: unable to get local issuer certificate"))


def test_http_403_is_egress_block():
    with pytest.raises(EgressBlocked, match="egress_block: Duval HTTP 403"):
        _run(_Resp("no", status=403))


def test_reset_is_connection_reset():
    with pytest.raises(EgressBlocked, match="connection_reset"):
        _run(requests.exceptions.ConnectionError("Connection aborted.", ConnectionResetError(104, "reset")))


@pytest.mark.parametrize("status,expected", [(404, ERROR_URL_CHANGED), (503, ERROR_NETWORK)])
def test_other_http_failures_classified_by_status(status, expected):
    with pytest.raises(DuvalHTTPError) as ei:
        _run(_Resp("err", status=status))
    assert not isinstance(ei.value, ParseDriftError)
    assert classify_exception(ei.value).error_class == expected


def test_changed_page_is_parse_drift_not_swallowed():
    with pytest.raises(ParseDriftError) as ei:
        _run(_Resp("<html><app-root></app-root></html>"))
    assert classify_exception(ei.value).error_class == ERROR_PARSE_DRIFT


def test_bad_payload_is_parse_drift():
    with pytest.raises(ParseDriftError):
        _s()._extract_from_api({"unexpected": {}})
    with pytest.raises(ParseDriftError):
        _s()._extract_from_api({"data": ["not-an-object"]})


# ── published-only records ──────────────────────────────────────────────────
def test_only_published_booking_numbers_are_keys():
    recs = _s()._extract_from_api({"data": [
        {"name": "DOE, J", "id": "77", "inmateId": "88", "jacketNumber": "99"},
        {"name": "ROE, K", "bookingNumber": "2026000002"},
    ]})
    assert [r.Booking_Number for r in recs] == ["2026000002"]


def test_status_source_recorded_and_unknowns_blank():
    rel, pub, none = _s()._extract_from_api({"data": [
        {"bookingNumber": "1", "releaseDate": "10/01/2026"},
        {"bookingNumber": "2", "inmateStatus": "IN JAIL"},
        {"bookingNumber": "3"},
    ]})
    assert (rel.Status, rel.extra_data.get("status_source")) == ("Released", "published_release_date")
    assert (pub.Status, pub.extra_data.get("status_source")) == ("IN JAIL", "published_status_field")
    assert (none.Status, none.Bond_Amount, none.Charges) == ("", "", "")
    assert "status_source" not in none.extra_data


def test_bond_published_zero_kept_and_charges_joined():
    rec = _s()._extract_from_api({"data": [{"bookingNumber": "1", "bond": 0,
                                            "charges": [{"description": "DUI"}, {"description": "RESIST"}]}]})[0]
    assert (rec.Bond_Amount, rec.Charges) == ("0", "DUI | RESIST")


def test_dom_requires_labelled_booking_and_bond():
    html = ("<table><tr><td>Doe, John</td><td>Ref 123456</td><td>Fine $50.00</td></tr>"
            "<tr><td>Roe, Kay</td><td>Booking Number: 2026000003</td><td>Bond: $1,500.00</td></tr></table>")
    recs = _s()._parse_dom(html)
    assert [(r.Booking_Number, r.Bond_Amount, r.Status) for r in recs] == [("2026000003", "1500", "")]
