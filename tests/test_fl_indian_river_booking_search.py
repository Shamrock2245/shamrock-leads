"""Indian River (FL) booking search + booking-details contract, 2026-10-08.

Synthetic fixtures (placeholder names, well-formed fake booking numbers);
no network access.
"""
from __future__ import annotations

from datetime import datetime

import pytest
import requests

from scrapers.counties import indian_river as ir
from scrapers.counties.indian_river import IndianRiverContractError, IndianRiverCountyScraper

LANDING = """
<form action="booking-search/search" method="POST" id="searchform">
<input type="text" name="lname"><input type="text" name="fname">
<input type="text" name="booking_date"><input type="text" name="booking_number">
<input type="hidden" name="_token" value="tok123">
</form>
"""


def _results(ids):
    return "".join(f'<a href="https://www.ircsheriff.org/booking-details/{i}">DOE, JANE</a>' for i in ids)


def _detail(booking="2026-00001234", booked="October 6th, 2026 at 9:05 pm", bond="$2,500.00", release=None,
            charges=("BATTERY", "RESIST WITHOUT VIOLENCE")):
    rows = [("Name", "DOE, JANE Q"), ("Date of Birth", "May 21, 2003 ( Age at booking: 23)"), ("Race", "White"), ("Sex", "Female")]
    info = [("Booking Date", booked), ("Arrest Date", "October 6th, 2026 at 8:40 pm"),
            ("Arresting Agency", "Example Police Department"), ("Booking Number", booking),
            ("Case Number", "2026-00009999"), ("Commissary Number", "123456")]
    if bond is not None:
        info.append(("Bond", bond))
    if release:
        info.append(("Release Date", release))
    table = lambda items: "<table><tbody>" + "".join(  # noqa: E731
        f'<tr><td class="text-right"><strong>{k}</strong></td><td>{v}</td></tr>' for k, v in items) + "</tbody></table>"
    cards = "".join(f'<div class="card"><div class="card-header">{c}</div><div class="card-body"><p><strong>Disposition:</strong>Pending</p></div></div>' for c in charges)
    return f"<h3>Bookings</h3>{table(rows)}<h3>Booking Info</h3>{table(info)}<h3>Charges</h3>{cards}"


def test_token_and_form_contract():
    assert ir.parse_token(LANDING) == "tok123"
    with pytest.raises(IndianRiverContractError):
        ir.parse_token("<form id='searchform'></form>")


def test_record_uses_detail_booking_number_not_portal_id():
    rec = ir.build_record(ir.parse_detail(_detail()), "https://www.ircsheriff.org/booking-details/730700001")
    assert rec.Booking_Number == "2026-00001234"
    assert rec.Booking_Date == "10/06/2026" and rec.Booking_Time == "09:05 PM"
    assert rec.Arrest_Date == "10/06/2026" and rec.Arrest_Time == "08:40 PM"
    assert rec.Last_Name == "DOE" and rec.First_Name == "JANE" and rec.DOB == "05/21/2003"
    assert rec.Charges == "BATTERY | RESIST WITHOUT VIOLENCE"
    assert rec.Bond_Amount == "2500.00" and rec.Status == "In Custody"
    assert rec.Case_Number == "2026-00009999" and rec.Sex == "F"


def test_bond_no_bond_and_unknown_never_zero():
    assert ir.build_record(ir.parse_detail(_detail(bond="No Bond")), "u").Bond_Amount == ""
    assert ir.build_record(ir.parse_detail(_detail(bond="No Bond")), "u").Bond_Type == "NO BOND"
    assert ir.build_record(ir.parse_detail(_detail(bond=None)), "u").Bond_Amount == ""
    assert ir.build_record(ir.parse_detail(_detail(bond="Pending")), "u").Bond_Amount == ""


def test_release_date_marks_released():
    rec = ir.build_record(ir.parse_detail(_detail(release="October 7th, 2026 at 10:00 am")), "u")
    assert rec.Status == "Released" and rec.Release_Date == "10/07/2026"


@pytest.mark.parametrize("booking", ["730700001", "2026-1234", "", "26-00001234"])
def test_missing_or_malformed_booking_number_is_dropped(booking):
    assert ir.build_record(ir.parse_detail(_detail(booking=booking)), "u") is None
    assert ir.build_record(ir.parse_detail(_detail(booked="")), "u") is None


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code != 200:
            raise requests.HTTPError(str(self.status_code))


def _install(monkeypatch, by_date, details):
    class _S:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None):
            if url == ir.SEARCH_PAGE_URL:
                return _Resp(LANDING)
            if url == ir.SEARCH_URL:
                pages = by_date.get(params["booking_date"], [])
                idx = params["page"] - 1
                return _Resp(_results(pages[idx]) if idx < len(pages) else "")
            return _Resp(details.get(url.rsplit("/", 1)[-1], ""), 200 if url.rsplit("/", 1)[-1] in details else 404)

        def post(self, url, data=None, timeout=None):
            assert data["_token"] == "tok123"
            pages = by_date.get(data["booking_date"], [])
            return _Resp(_results(pages[0]) if pages else "")

    monkeypatch.setattr(ir.requests, "Session", _S)
    monkeypatch.setattr(ir.time, "sleep", lambda *_: None)


class _FixedDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 7, 12, 0)


def test_scrape_pages_each_day_and_checks_booking_date(monkeypatch):
    monkeypatch.setattr(ir, "datetime", _FixedDT)
    page1 = [str(700000 + i) for i in range(10)]
    page2 = ["700100"]
    by_date = {"10/06/2026": [page1, page2], "10/07/2026": [["700200"]]}
    details = {i: _detail(booking=f"2026-{int(i):08d}") for i in page1 + page2}
    details["700200"] = _detail(booking="2026-00700200", booked="October 7th, 2026 at 1:00 am")
    details["700003"] = _detail(booking="2026-00700003", booked="September 1st, 2026 at 1:00 am")  # wrong day
    _install(monkeypatch, by_date, details)
    recs = IndianRiverCountyScraper().scrape(lookback_days=2)
    keys = {r.Booking_Number for r in recs}
    assert "2026-00700200" in keys and "2026-00700100" in keys
    assert "2026-00700003" not in keys
    assert len(recs) == 11


def test_scrape_fails_closed_when_no_detail_has_a_booking_number(monkeypatch):
    monkeypatch.setattr(ir, "datetime", _FixedDT)
    _install(monkeypatch, {"10/07/2026": [["1"]]}, {"1": _detail(booking="")})
    with pytest.raises(IndianRiverContractError):
        IndianRiverCountyScraper().scrape(lookback_days=1)


def test_single_booking_recheck_requires_same_source_key(monkeypatch):
    _install(monkeypatch, {}, {"730700001": _detail(booking="2026-00001234")})
    s = IndianRiverCountyScraper()
    url = "https://www.ircsheriff.org/booking-details/730700001"
    assert s._fetch_single_booking("2026-00001234", url).Booking_Number == "2026-00001234"
    assert s._fetch_single_booking("730700001", url) is None  # legacy portal-id key
    assert s._fetch_single_booking("2026-00001234", "https://evil.example/booking-details/1") is None


def test_module_uses_plain_requests_only():
    src = open(ir.__file__).read().split('"""', 2)[2]
    for banned in ("curl_cffi", "impersonate", "verify=False", "DrissionPage", "proxy", "captcha", "disable_warnings"):
        assert banned not in src, banned
