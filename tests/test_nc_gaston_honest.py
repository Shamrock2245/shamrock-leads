"""Gaston NC: honest plain_http; published-only charges/bond/status; source booking keys only."""
import inspect
from unittest import mock

import pytest

from scrapers import plain_http
from scrapers.counties_nc import gaston
from scrapers.counties_nc.gaston import GastonScraper, _HonestSession, listing_custody, parse_detail, status_fields
from scrapers.scraper_resilience import EgressBlocked
from tests.test_gap_queue_nc_tx_sc import GASTON_LISTING, _gaston_booking, _gaston_detail

URL = "https://tepsweb.cityofgastonia.com/NewWorld.InmateInquiry/GastonCounty/Inmate/Detail/-111"


def test_no_chrome_or_invented_defaults_in_source():
    src = inspect.getsource(gaston).split('"""', 2)[2]
    for banned in ("Chrome/", "verify=False", 'Status="In Custody"', 'or "Unknown"', 'else "0"', "curl_cffi"):
        assert banned not in src, banned


def test_honest_session_routes_through_plain_http(monkeypatch):
    seen = {}

    class R:
        status_code, text = 200, ""

    monkeypatch.setattr(gaston, "plain_request", lambda sess, m, u, county=None, **kw: seen.update(m=m, county=county) or R())
    s = _HonestSession()
    assert s.verify is True and s.headers["User-Agent"] == plain_http.HONEST_USER_AGENT
    s.get(gaston.PORTAL_URL)
    assert seen == {"m": "GET", "county": "Gaston"}


def test_listing_custody_column():
    assert listing_custody(GASTON_LISTING) == {URL: "Yes"}
    assert listing_custody(GASTON_LISTING.replace(">Yes<", ">No<")) == {URL: "No"}
    no_col = GASTON_LISTING.replace('<td class="InCustody">Yes</td>', "")
    assert listing_custody(no_col) == {URL: ""}


@pytest.mark.parametrize("cell,expected", [
    ("Yes", ("In Custody", "published_in_custody_column")),
    ("No", ("", "")), ("", ("", "")), ("maybe", ("", "")),
])
def test_status_only_from_published_column(cell, expected):
    assert status_fields(cell) == expected


def _bk(**kw):
    return parse_detail(_gaston_detail(_gaston_booking("2026-00001234", **kw)))[1][0]


def test_record_status_and_unknown_charges():
    rec = GastonScraper()._to_record("X", {}, _bk(), URL, "Yes")
    assert rec.Status == "In Custody" and rec.extra_data["status_source"] == "published_in_custody_column"
    assert GastonScraper()._to_record("X", {}, _bk(), URL, "").Status == ""
    assert GastonScraper()._to_record("X", {}, _bk(release="1/2/2026"), URL, "Yes").Status == ""  # released booking
    no_charges = dict(_bk(), charges="")
    assert GastonScraper()._to_record("X", {}, no_charges, URL, "Yes").Charges == ""


def test_bond_published_only():
    assert _bk(bond="$0.00")["bond"] == "0"  # published zero kept
    assert _bk(bond="$1,500.00")["bond"] == "1500"
    assert _bk(bond="")["bond"] == ""  # no total, blank per-bond amount -> unknown
    assert _bk(bond="PENDING")["bond"] == ""


def test_detail_block_raises():
    def fake_get(url, params=None, timeout=None):
        if "Detail" in url:
            raise EgressBlocked("egress_block: Gaston http_403")
        body = GASTON_LISTING.replace(">Next<", "><") + "<input name='BookingFromDate'><title>Inmate Search</title>"
        return mock.Mock(text=body, raise_for_status=lambda: None)

    with mock.patch.object(gaston, "_HonestSession") as cls:
        cls.return_value.headers = {}
        cls.return_value.get.side_effect = fake_get
        s = GastonScraper()
        s.DETAIL_DELAY_S = 0
        with pytest.raises(EgressBlocked):
            s.scrape()


@pytest.mark.parametrize("number", ["123456", "2026-1", "GAS_2026", "SUBJ-123456", ""])
def test_only_source_booking_heading_keys(number):
    def fake_get(url, params=None, timeout=None):
        if "Detail" in url:
            return mock.Mock(text=_gaston_detail(_gaston_booking(number)), raise_for_status=lambda: None)
        body = GASTON_LISTING.replace(">Next<", "><") + "<input name='BookingFromDate'><title>Inmate Search</title>"
        return mock.Mock(text=body, raise_for_status=lambda: None)

    with mock.patch.object(gaston, "_HonestSession") as cls:
        cls.return_value.headers = {}
        cls.return_value.get.side_effect = fake_get
        s = GastonScraper()
        s.DETAIL_DELAY_S = 0
        with pytest.raises(gaston.ParseDriftError):  # no source key -> nothing emitted, run fails visibly
            s.scrape()


def test_docstring_cites_recon():
    assert "NC_GASTON_ROSTER_SCOPE_2026-10-15" in gaston.__doc__
