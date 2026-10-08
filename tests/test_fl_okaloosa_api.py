"""Okaloosa (FL) Inmate Locator JSON contract, 2026-10-08.

Synthetic fixtures (placeholder names, well-formed fake booking numbers);
no network access.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import requests

from scrapers.counties import okaloosa
from scrapers.counties.okaloosa import OkaloosaContractError, OkaloosaCountyScraper


def _row(booking, when: datetime, bond=0.0, status="1", name="DOE, JANE Q"):
    return {
        "bookingNo": booking, "fullName": name, "custodyDate": when.strftime("%Y-%m-%dT%H:%M:%S"),
        "totalBondAmt": bond, "status": status, "sex": "F", "race": "W",
        "dobDttm": "1990-01-15T00:00:00", "spnNo": 12345, "nameID": 1, "height1": 505, "weight1": 120,
    }


def _detail(booking, charges):
    return {"bookingNo": booking, "charges": charges, "releaseDate": None, "status": "1"}


def test_search_page_shape_is_enforced():
    total, rows = okaloosa.parse_search_page({"total": 1, "page": 1, "pageSize": 10, "data": [_row("2026000001", datetime(2026, 10, 7))]})
    assert total == 1 and rows[0]["bookingNo"] == "2026000001"
    for bad in ({"data": []}, {"total": 1, "data": {}}, {"total": 1, "data": [{"fullName": "X"}]}, []):
        with pytest.raises(OkaloosaContractError):
            okaloosa.parse_search_page(bad)


def test_record_uses_source_booking_and_custody_date():
    rec = okaloosa.build_record(_row("2026000001", datetime(2026, 10, 7, 21, 5), bond=2500.0), None)
    assert rec.Booking_Number == "2026000001"
    assert rec.Booking_Date == "10/07/2026" and rec.Booking_Time == "09:05 PM"
    assert rec.Last_Name == "DOE" and rec.First_Name == "JANE" and rec.Middle_Name == "Q"
    assert rec.DOB == "01/15/1990" and rec.Sex == "F" and rec.Person_ID == "12345"
    assert rec.Bond_Amount == "" and rec.Status == "In Custody"  # no detail: roster total not trusted


def test_zero_roster_bond_is_unknown_not_zero():
    rec = okaloosa.build_record(_row("2026000001", datetime(2026, 10, 7), bond=0), None)
    assert rec.Bond_Amount == ""


@pytest.mark.parametrize("booking", ["202600001", "ABCD000001", ""])
def test_malformed_booking_or_missing_date_is_not_emitted(booking):
    assert okaloosa.build_record(_row(booking, datetime(2026, 10, 7)), None) is None
    row = _row("2026000001", datetime(2026, 10, 7))
    row["custodyDate"] = ""
    assert okaloosa.build_record(row, None) is None


def test_detail_charges_bond_and_mismatch():
    det = okaloosa.parse_detail(_detail("2026000001", [
        {"charge": "784.03", "chargeDesc": "BATTERY", "severity": "MF", "bailAmt": 1500.0, "bailType": "SURETY", "caseNbr": "26-000001", "courtDate": None},
        {"charge": "843.02", "chargeDesc": "RESIST W/O VIOLENCE", "severity": "MF", "bailAmt": None, "bailType": "", "caseNbr": "26-000001", "courtDate": None},
    ]), "2026000001")
    assert det["charges"] == ["BATTERY", "RESIST W/O VIOLENCE"]
    assert det["bond"] == ""  # one blank charge (maybe a hold): total unknown, not 1500.00
    assert [d["bond_amount"] for d in det["details"]] == [1500.0, None]
    assert det["details"][0]["statute"] == "784.03" and det["details"][0]["degree"] == "MF"
    assert det["case_numbers"] == ["26-000001"]
    assert okaloosa.parse_detail(_detail("2026000002", []), "2026000001") is None
    none_published = okaloosa.parse_detail(_detail("2026000001", [{"chargeDesc": "VOP", "bailAmt": None}]), "2026000001")
    assert none_published["bond"] == ""
    rec = okaloosa.build_record(_row("2026000001", datetime(2026, 10, 7), bond=0), none_published)
    assert rec.Bond_Amount == "" and rec.Charges == "VOP"
    assert rec.extra_data["charge_details"][0]["bond_amount"] is None


def _bonds(*amts):
    return okaloosa.parse_detail(
        _detail("2026000001", [{"chargeDesc": f"C{i}", "bailAmt": a} for i, a in enumerate(amts)]), "2026000001")


def test_total_bond_empty_when_any_charge_bond_is_blank():
    mixed = _bonds(1500.0, None)
    assert mixed["bond"] == ""
    assert [d["bond_amount"] for d in mixed["details"]] == [1500.0, None]  # known per-charge amount kept
    assert _bonds(1500.0, 250.0)["bond"] == "1750.00"
    assert _bonds(None, None)["bond"] == ""
    assert _bonds(0.0, 1500.0)["bond"] == "1500.00"  # published $0 is known
    # the roster total (sum of published bailAmt) must not fill in for a blank charge
    rec = okaloosa.build_record(_row("2026000001", datetime(2026, 10, 7), bond=1500.0), mixed)
    assert rec.Bond_Amount == ""
    # no detail fetched (outside the window): the roster total cannot show a blank charge
    assert okaloosa.build_record(_row("2026000001", datetime(2026, 10, 7), bond=1500.0), None).Bond_Amount == ""


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code != 200:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._data


def _install(monkeypatch, pages, details, calls):
    class _S:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None):
            calls.append((url, dict(params or {})))
            if url == okaloosa.SEARCH_URL:
                page = params["page"]
                return _Resp(pages[page - 1]) if page - 1 < len(pages) else _Resp(None, 500)
            booking = url.rsplit("/", 1)[-1]
            return _Resp(details.get(booking, {}))

    monkeypatch.setattr(okaloosa.requests, "Session", _S)
    monkeypatch.setattr(okaloosa.time, "sleep", lambda *_: None)


def test_scrape_pages_to_total_details_only_recent_and_skips_unknown_status(monkeypatch):
    now = datetime.now()
    monkeypatch.setattr(okaloosa, "PAGE_SIZE", 2)
    rows = [
        _row("2026000004", now - timedelta(hours=2), bond=0),
        _row("2026000003", now - timedelta(days=1), bond=750.0),
        _row("2025000002", now - timedelta(days=200), bond=0),
        _row("2026000005", now - timedelta(hours=1), status="2"),
    ]
    pages = [{"total": 4, "page": 1, "pageSize": 2, "data": rows[:2]},
             {"total": 4, "page": 2, "pageSize": 2, "data": rows[2:]}]
    details = {
        "2026000004": _detail("2026000004", [{"chargeDesc": "DUI", "bailAmt": 500.0}]),
        "2026000003": _detail("2026000003", [{"chargeDesc": "THEFT", "bailAmt": 750.0}]),
    }
    calls: list = []
    _install(monkeypatch, pages, details, calls)
    recs = OkaloosaCountyScraper().scrape(lookback_days=7)
    assert [r.Booking_Number for r in recs] == ["2026000004", "2026000003", "2025000002"]
    by = {r.Booking_Number: r for r in recs}
    assert by["2026000004"].Bond_Amount == "500.00" and by["2026000004"].Charges == "DUI"
    assert by["2025000002"].Bond_Amount == "" and by["2025000002"].Charges == ""
    detail_calls = [u for u, _ in calls if u != okaloosa.SEARCH_URL]
    assert sorted(u.rsplit("/", 1)[-1] for u in detail_calls) == ["2026000003", "2026000004"]


def test_scrape_fails_closed_on_short_walk_duplicates_and_errors(monkeypatch):
    now = datetime.now()
    short = [{"total": 3, "page": 1, "pageSize": 100, "data": [_row("2026000001", now)]},
             {"total": 3, "page": 2, "pageSize": 100, "data": []}]
    _install(monkeypatch, short, {}, [])
    with pytest.raises(OkaloosaContractError):
        OkaloosaCountyScraper().scrape()
    dup = [{"total": 2, "page": 1, "pageSize": 100, "data": [_row("2026000001", now), _row("2026000001", now)]}]
    _install(monkeypatch, dup, {}, [])
    with pytest.raises(OkaloosaContractError):
        OkaloosaCountyScraper().scrape()
    _install(monkeypatch, [], {}, [])
    with pytest.raises(OkaloosaContractError):
        OkaloosaCountyScraper().scrape()


def test_module_uses_plain_requests_only():
    src = open(okaloosa.__file__).read().split('"""', 2)[2]
    for banned in ("curl_cffi", "impersonate", "verify=False", "DrissionPage", "proxy", "captcha", "Default.aspx"):
        assert banned not in src, banned
