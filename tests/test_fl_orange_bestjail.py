"""Orange (FL) BestJail JSON contract, 2026-10-08.

Synthetic fixtures (placeholder names, well-formed fake booking numbers);
no network access.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import string

import pytest
import requests

from scrapers.counties import orange
from scrapers.counties.orange import OrangeContractError, OrangeCountyScraper


def _fmt(dt: datetime):
    return dt.strftime("%m/%d/%Y"), dt.strftime("%I:%M%p").lstrip("0").lower()


def _detail(booking: str, dt: datetime, name="DOE, JANE Q"):
    d, t = _fmt(dt)
    return [{
        "BOOKING": booking, "NAME": name, "DATEBOOKED": d, "TIMEBOOKED": t,
        "BIRTH": "34", "GENDER": "FEMALE", "RACE": "W", "CELL": "X1",
        "STREET": "1 MAIN ST", "APTNUM": "", "CITY": "ORLANDO", "STATE": "FL", "ZIPCODE": "32801",
        "SSN": "", "IMAGE": "", "HOLDS": "0", "HasImmigrationHold": "false",
    }]


def _pages(rows_by_letter):
    pages = {letter: [{"bookingNumber": "99000000", "inmateName": f"{letter.upper()}ALIAS, X"}] for letter in string.ascii_lowercase}
    pages.update(rows_by_letter)
    return pages


def test_roster_dedupes_alias_rows_and_sorts_newest_first():
    pages = {
        "a": [{"bookingNumber": "26000002", "inmateName": "ALPHA, ONE"},
              {"bookingNumber": "25000009 ", "inmateName": "ALPHA, TWO"}],
        "p": [{"bookingNumber": "26000002", "inmateName": "PSEUDONYM, ONE"}],
    }
    assert orange.parse_roster(pages) == [("26000002", "ALPHA, ONE"), ("25000009", "ALPHA, TWO")]


@pytest.mark.parametrize("bad", ["2600002", "ABC12345", "", "26-000002"])
def test_roster_rejects_malformed_booking_numbers(bad):
    with pytest.raises(OrangeContractError):
        orange.parse_roster({"a": [{"bookingNumber": bad, "inmateName": "ALPHA, ONE"}]})


def test_roster_rejects_shape_drift_and_many_empty_letters():
    with pytest.raises(OrangeContractError):
        orange.parse_roster({"a": {"rows": []}})
    with pytest.raises(OrangeContractError):
        orange.parse_roster({"a": [{"booking": "26000001", "name": "X"}]})
    pages = {letter: [] for letter in "abcd"}
    pages["e"] = [{"bookingNumber": "26000001", "inmateName": "E, X"}]
    with pytest.raises(OrangeContractError):
        orange.parse_roster(pages)


def test_detail_must_name_the_requested_booking():
    now = datetime(2026, 10, 7, 21, 5)
    assert orange.parse_detail(_detail("26000001", now), "26000001")["NAME"] == "DOE, JANE Q"
    assert orange.parse_detail(_detail("26000099", now), "26000001") is None
    assert orange.parse_detail([], "26000001") is None
    assert orange.parse_booked_at({"DATEBOOKED": "10/07/2026", "TIMEBOOKED": "9:05pm"}) == now
    assert orange.parse_booked_at({"DATEBOOKED": "", "TIMEBOOKED": "9:05pm"}) is None


def test_bond_is_published_sum_and_unknown_stays_empty():
    charges = [
        {"Charge": "BATTERY", "BondAmount": "1500.00", "ArrestingAgency": "OCSO", "CourtCaseNumber": "2026MM1", "CaseStatus": "PRESENTENCED"},
        {"Charge": "RESIST W/O VIOLENCE", "BondAmount": "500.00", "ArrestingAgency": "OCSO", "CourtCaseNumber": "2026MM1", "CaseStatus": "PRESENTENCED"},
        {"Charge": "VOP", "BondAmount": "", "ArrestingAgency": "OCSO", "CourtCaseNumber": "", "CaseStatus": "PRESENTENCED"},
    ]
    out = orange.parse_charges(charges)
    assert out["bond"] == ""  # one blank charge (maybe a hold): total unknown, not 2000.00
    assert out["charges"] == ["BATTERY", "RESIST W/O VIOLENCE", "VOP"]
    assert [d["bond_amount"] for d in out["details"]] == [1500.0, 500.0, None]
    assert out["case_numbers"] == ["2026MM1"]
    assert orange.parse_charges([{"Charge": "VOP", "BondAmount": "0.00"}])["bond"] == "0.00"
    assert orange.parse_charges([{"Charge": "VOP", "BondAmount": ""}])["bond"] == ""
    assert orange.parse_charges([])["bond"] == ""
    assert orange.parse_charges(None)["bond"] == ""


def _c(*bonds):
    return [{"Charge": f"C{i}", "BondAmount": b} for i, b in enumerate(bonds)]


def test_total_bond_empty_when_any_charge_bond_is_blank():
    mixed = orange.parse_charges(_c("1500.00", ""))
    assert mixed["bond"] == ""
    assert [d["bond_amount"] for d in mixed["details"]] == [1500.0, None]  # known per-charge amount kept
    assert orange.parse_charges(_c("1500.00", "250.00"))["bond"] == "1750.00"
    assert orange.parse_charges(_c("", " "))["bond"] == ""
    assert orange.parse_charges(_c("0.00", "1500.00"))["bond"] == "1500.00"  # published $0 is known
    assert orange.parse_charges(_c("1500.00", "SEE NOTE"))["bond"] == ""  # unparsed counts as unpublished
    rec = orange.build_record("26000001", "X", _detail("26000001", datetime(2026, 10, 7, 9))[0],
                              orange.parse_charges(_c("1500.00", "")))
    assert rec.Bond_Amount == ""


def test_record_fields_age_not_dob_and_no_invented_values():
    booked = datetime(2026, 10, 7, 21, 5)
    rec = orange.build_record(
        "26000001", "ALIAS, X", _detail("26000001", booked)[0],
        orange.parse_charges([{"Charge": "BATTERY", "BondAmount": "", "ArrestingAgency": "OCSO"}]),
    )
    assert rec.Booking_Number == "26000001"
    assert rec.Full_Name == "DOE, JANE Q" and rec.Last_Name == "DOE" and rec.First_Name == "JANE"
    assert rec.Booking_Date == "10/07/2026" and rec.Booking_Time == "09:05 PM"
    assert rec.DOB == "" and rec.Age_At_Arrest == "34"
    assert rec.Bond_Amount == "" and rec.Charges == "BATTERY"
    assert rec.Status == "In Custody" and rec.Sex == "F"
    assert rec.extra_data["charge_details"][0]["bond_amount"] is None
    no_date = _detail("26000001", booked)[0] | {"DATEBOOKED": ""}
    assert orange.build_record("26000001", "X", no_date, orange.parse_charges([])) is None


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code != 200:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._data


def _fake_session(monkeypatch, pages, details, charges, calls):
    class _S:
        def __init__(self):
            self.headers = {}

        def get(self, url, timeout=None):
            calls.append(url)
            assert "verify" not in url
            tail = url.rsplit("/", 1)[-1]
            if "/getInmates/" in url:
                return _Resp(pages[tail]) if tail in pages else _Resp(None, 500)
            if "/getInmateDetails/" in url:
                return _Resp(details.get(tail, []))
            if "/getCharges/" in url:
                return _Resp(charges.get(tail, []))
            raise AssertionError(url)

    monkeypatch.setattr(orange.requests, "Session", _S)
    monkeypatch.setattr(orange.time, "sleep", lambda *_: None)


def test_scrape_walks_newest_first_and_stops_after_window(monkeypatch):
    now = datetime.now()
    monkeypatch.setattr(orange, "STOP_AFTER_OLDER", 2)
    newest = ["26000010", "26000009", "26000008"]
    older = ["26000007", "26000006", "26000005", "26000004"]
    pages = _pages({"d": [{"bookingNumber": b, "inmateName": "DOE, J"} for b in newest + older]})
    details = {b: _detail(b, now - timedelta(hours=i + 1)) for i, b in enumerate(newest)}
    details.update({b: _detail(b, now - timedelta(days=30)) for b in older})
    charges = {b: [{"Charge": "BATTERY", "BondAmount": "250.00"}] for b in newest}
    calls: list = []
    _fake_session(monkeypatch, pages, details, charges, calls)
    records = OrangeCountyScraper().scrape(lookback_days=7)
    assert [r.Booking_Number for r in records] == newest
    detail_calls = [c for c in calls if "/getInmateDetails/" in c]
    assert "26000005" not in " ".join(detail_calls)  # stopped after 2 older in a row
    assert all(r.Bond_Amount == "250.00" for r in records)
    charge_calls = {c.rsplit("/", 1)[-1] for c in calls if "/getCharges/" in c}
    assert charge_calls == set(newest)  # no charges fetched for out-of-window bookings


def test_scrape_raises_on_letter_failure_and_all_detail_failures(monkeypatch):
    pages = _pages({})
    del pages["q"]
    _fake_session(monkeypatch, pages, {}, {}, [])
    with pytest.raises(OrangeContractError):
        OrangeCountyScraper().scrape()
    pages = _pages({"d": [{"bookingNumber": "26000001", "inmateName": "DOE, J"}]})
    _fake_session(monkeypatch, pages, {}, {}, [])
    with pytest.raises(OrangeContractError):
        OrangeCountyScraper().scrape()


def test_module_uses_plain_requests_only():
    src = open(orange.__file__).read()
    for banned in ("curl_cffi", "impersonate", "verify=False", "DrissionPage", "proxy", "captcha"):
        assert banned not in src.split('"""', 2)[2], banned


def test_charges_shape_drift_raises_but_fetch_failure_is_unknown():
    # a per-booking fetch failure (None) leaves charges/bond unknown
    assert orange.parse_charges(None)["bond"] == ""
    # a successful response with a changed shape is drift, never blank data
    for bad in ({"charges": []}, "oops", [["BATTERY", "250.00"]], [{"ChargeDesc": "BATTERY", "BondAmount": "1"}],
                [{"Charge": "BATTERY", "Bond": "250.00"}]):
        with pytest.raises(OrangeContractError):
            orange.parse_charges(bad)


def test_scrape_raises_on_charges_drift_and_when_every_charges_fetch_fails(monkeypatch):
    now = datetime.now()
    pages = _pages({"d": [{"bookingNumber": "26000002", "inmateName": "DOE, J"}]})
    details = {"26000002": _detail("26000002", now - timedelta(hours=1))}
    _fake_session(monkeypatch, pages, details, {"26000002": {"renamed": []}}, [])
    with pytest.raises(OrangeContractError):
        OrangeCountyScraper().scrape(lookback_days=7)

    def _get_json(self, session, url):
        if "/getCharges/" in url:
            raise requests.ConnectionError("down")
        return _orig(self, session, url)

    _fake_session(monkeypatch, pages, details, {}, [])
    _orig = OrangeCountyScraper._get_json
    monkeypatch.setattr(OrangeCountyScraper, "_get_json", _get_json)
    with pytest.raises(OrangeContractError):
        OrangeCountyScraper().scrape(lookback_days=7)


def test_roster_and_detail_requests_are_paced(monkeypatch):
    now = datetime.now()
    pages = _pages({"d": [{"bookingNumber": b, "inmateName": "DOE, J"} for b in ("26000003", "26000002")]})
    details = {b: _detail(b, now - timedelta(hours=1)) for b in ("26000003", "26000002")}
    charges = {b: [{"Charge": "BATTERY", "BondAmount": "100.00"}] for b in details}
    calls: list = []
    _fake_session(monkeypatch, pages, details, charges, calls)
    sleeps: list = []
    monkeypatch.setattr(orange.time, "sleep", lambda s: sleeps.append(s))
    OrangeCountyScraper().scrape(lookback_days=7)
    assert orange.ROSTER_PAUSE_S >= 0.5 and orange.REQUEST_PAUSE_S >= 0.25
    assert sleeps.count(orange.ROSTER_PAUSE_S) >= 25  # between every getInmates letter
    later = [c for c in calls if "/getInmates/" not in c]
    assert len([s for s in sleeps if s == orange.REQUEST_PAUSE_S]) >= len(later)  # one pause per detail/charges call


def test_one_failed_charges_fetch_skips_that_booking_not_blanks_it(monkeypatch):
    now = datetime.now()
    books = ["26000003", "26000002"]
    pages = _pages({"d": [{"bookingNumber": b, "inmateName": "DOE, J"} for b in books]})
    details = {b: _detail(b, now - timedelta(hours=1)) for b in books}
    charges = {"26000002": [{"Charge": "BATTERY", "BondAmount": "500.00"}]}
    _fake_session(monkeypatch, pages, details, charges, [])
    _orig = OrangeCountyScraper._get_json

    def _get_json(self, session, url):
        if url.endswith("/getCharges/26000003"):
            raise requests.ConnectionError("down")
        return _orig(self, session, url)

    monkeypatch.setattr(OrangeCountyScraper, "_get_json", _get_json)
    recs = OrangeCountyScraper().scrape(lookback_days=7)
    # 26000003 is not emitted at all (no blank charges/bond written over stored values)
    assert [r.Booking_Number for r in recs] == ["26000002"]
    assert recs[0].Charges == "BATTERY" and recs[0].Bond_Amount == "500.00"

