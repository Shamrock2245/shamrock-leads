"""Lee FL: empty charges API → unknown bond (\"\"), never invented \"0\" (2026-10-08).

Stacked on #147 (plain direct fetch). A booking with no charges response (or an
empty charges list) must leave Bond_Amount blank so to_mongo_doc stores
bond_amount_raw \"\" / bond_amount 0.0 without claiming a published zero.
"""
from __future__ import annotations

from scrapers.counties.lee import LeeCountyScraper


def _booking(**extra):
    row = {
        "id": "12345678901234",
        "bookingNumber": "1234567",
        "bookingDate": "2026-10-08 12:00:00.000",
        "givenName": "TEST",
        "surName": "PERSON",
        "middleName": "",
        "suffixName": "",
        "inCustody": True,
        "address": "",
        "image": "",
    }
    row.update(extra)
    return row


def test_normalize_without_enrichment_leaves_bond_unknown():
    n = LeeCountyScraper()._normalize_record(_booking())
    assert n is not None
    assert n["bond_amount"] == ""
    assert LeeCountyScraper()._to_arrest_record(n).Bond_Amount == ""


def test_to_arrest_record_defaults_missing_bond_to_empty():
    n = LeeCountyScraper()._normalize_record(_booking())
    del n["bond_amount"]
    assert LeeCountyScraper()._to_arrest_record(n).Bond_Amount == ""


def test_parse_charges_empty_list_is_unknown_not_zero():
    parsed = LeeCountyScraper._parse_charges([])
    assert parsed["bond_amount"] == ""
    assert parsed["charges"] == []


def test_fetch_single_booking_empty_charges_bond_is_unknown(monkeypatch):
    scraper = LeeCountyScraper()

    class _Resp:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_fetch(url, params=None):
        if "/charges" in url:
            return _Resp([])
        return _Resp(_booking(id="1234567", bookingNumber="1234567"))

    monkeypatch.setattr(scraper, "_http_fetch", fake_fetch)
    monkeypatch.setattr("dashboard.services.url_ingest_service._title_case_name", lambda s: s.title())
    monkeypatch.setattr("dashboard.services.url_ingest_service.FL_COUNTIES_UPPER", {"LEE"})
    rec = scraper._fetch_single_booking("1234567", "https://www.sheriffleefl.org/booking/?id=1234567")
    assert rec is not None
    assert rec.Bond_Amount == ""
    assert rec.Booking_Number == "1234567"


def test_published_positive_bond_still_sums():
    parsed = LeeCountyScraper._parse_charges([
        {"offenseDescription": "BATTERY ON PERSON", "bondAmount": "500"},
        {"offenseDescription": "RESISTING WITHOUT VIOLENCE", "bondAmount": 1500},
    ])
    assert parsed["bond_amount"] == "2000.00"
