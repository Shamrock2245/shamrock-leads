"""Indian River (FL): honest UA + TLS verified via scrapers.plain_http; the
per-record bond guard (booking-scoped fields only from Booking Info) and the
card-header guard (top-level Charges cards only). Synthetic HTML, no network."""
from __future__ import annotations

import inspect

import pytest
import requests

import scrapers.counties.indian_river as ir
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.scraper_resilience import EgressBlocked


def _table(items):
    return "<table><tbody>" + "".join(
        f'<tr><td><strong>{k}</strong></td><td>{v}</td></tr>' for k, v in items) + "</tbody></table>"


PERSON = [("Name", "DOE, JANE Q"), ("Date of Birth", "May 21, 2003"), ("Race", "White"), ("Sex", "Female")]
INFO = [("Booking Date", "October 6th, 2026 at 9:05 pm"), ("Booking Number", "2026-00001234"),
        ("Arresting Agency", "Example PD"), ("Case Number", "2026-00009999")]
CARDS = '<div class="card"><div class="card-header">BATTERY</div></div>'


def _page(bookings_extra=(), info_extra=(), cards=CARDS):
    return (f"<h3>Bookings</h3>{_table(PERSON + list(bookings_extra))}"
            f"<h3>Booking Info</h3>{_table(INFO + list(info_extra))}<h3>Charges</h3>{cards}")


def test_no_browser_ua_in_module():
    src = inspect.getsource(ir).split('"""', 2)[2]
    for banned in ("Mozilla/", "Chrome/", "curl_cffi", "impersonate", "verify=False"):
        assert banned not in src, banned


def test_bond_outside_booking_info_never_bleeds_in():
    # A Bond (and another booking number) in the Bookings history table must not
    # become this record's bond: no Bond row in Booking Info means "no Bond row".
    d = ir.parse_detail(_page(bookings_extra=[("Bond", "$9,999.00"), ("Booking Number", "2025-00000001")]))
    assert "Bond" not in d["fields"]
    assert d["fields"]["Booking Number"] == "2026-00001234"


def test_bond_from_booking_info_is_used():
    d = ir.parse_detail(_page(bookings_extra=[("Bond", "$9,999.00")], info_extra=[("Bond", "$500.00")]))
    rec = ir.build_record(d, "u")
    assert rec.Bond_Amount == "500.00"


def test_conflicting_bond_rows_are_ambiguous():
    d = ir.parse_detail(_page(info_extra=[("Bond", "$500.00"), ("Bond", "$1,000.00")]))
    assert d["ambiguous"] == ["Bond"]


def test_repeated_identical_row_is_not_ambiguous():
    d = ir.parse_detail(_page(info_extra=[("Bond", "$500.00"), ("Bond", "$500.00")]))
    assert d["ambiguous"] == []


@pytest.mark.parametrize("bond,amount", [("", ""), ("No Bond", ""), ("Pending", ""), ("$0.00", "0.00")])
def test_unknown_bond_is_blank_published_zero_kept(bond, amount):
    rec = ir.build_record(ir.parse_detail(_page(info_extra=[("Bond", bond)])), "u")
    assert rec.Bond_Amount == amount


def test_nested_card_is_not_a_separate_charge():
    cards = ('<div class="card"><div class="card-header">THEFT</div>'
             '<div class="card-body"><div class="card"><div class="card-header">Sub detail</div></div></div></div>'
             '<div class="card"><div class="card-header">BATTERY</div></div>')
    assert ir.parse_detail(_page(cards=cards))["charges"] == ["THEFT", "BATTERY"]


def test_recheck_skips_ambiguous_page(monkeypatch):
    page = _page(info_extra=[("Bond", "$500.00"), ("Bond", "$1,000.00")])

    def _req(sess, method, url, **kw):
        r = requests.Response()
        r.status_code, r._content = 200, page.encode()
        r.headers["content-type"] = "text/html"
        return r

    monkeypatch.setattr(requests.Session, "request", _req)
    assert ir.IndianRiverCountyScraper()._fetch_single_booking(
        "2026-00001234", "https://www.ircsheriff.org/booking-details/9") is None


def test_session_is_honest_and_verified():
    s = ir.IndianRiverCountyScraper()._session()
    assert s.headers["User-Agent"] == HONEST_USER_AGENT and s.verify is True


@pytest.mark.parametrize("exc,etype", [
    (requests.exceptions.SSLError("certificate verify failed"), SourceTLSInvalid),
])
def test_tls_failure_raises(monkeypatch, exc, etype):
    def _req(*_a, **_k):
        raise exc

    monkeypatch.setattr(requests.Session, "request", _req)
    with pytest.raises(etype):
        ir.IndianRiverCountyScraper().scrape(lookback_days=1)


@pytest.mark.parametrize("code", [401, 403, 429])
def test_block_raises_on_landing_and_recheck(monkeypatch, code):
    def _req(sess, method, url, **kw):
        r = requests.Response()
        r.status_code, r._content = code, b"denied"
        r.headers["content-type"] = "text/html"
        return r

    monkeypatch.setattr(requests.Session, "request", _req)
    with pytest.raises(EgressBlocked):
        ir.IndianRiverCountyScraper().scrape(lookback_days=1)
    with pytest.raises(EgressBlocked):
        ir.IndianRiverCountyScraper()._fetch_single_booking(
            "2026-00001234", "https://www.ircsheriff.org/booking-details/9")
