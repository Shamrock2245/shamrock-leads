import unittest
from unittest.mock import patch

from scrapers.counties_sc.anderson import AndersonScraper
from scrapers.counties_sc.cherokee import CherokeeScraper
from scrapers.counties_sc.colleton import ColletonScraper
from scrapers.counties_sc.kershaw import KershawScraper
from scrapers.counties_sc.laurens import LaurensScraper
from scrapers.zuercher_base import ZuercherBaseScraper


GUARDED_SCRAPERS = (
    AndersonScraper,
    CherokeeScraper,
    ColletonScraper,
    KershawScraper,
    LaurensScraper,
)


class _FakeResponse:
    status_code = 200

    def __init__(self, payload=None):
        self._payload = payload if payload is not None else {"records": [], "total_record_count": 0}

    def json(self):
        return self._payload


class _FakeSession:
    headers = {}

    def get(self, *args, **kwargs):
        return _FakeResponse()

    def post(self, *args, **kwargs):
        return _FakeResponse(
            {
                "total_record_count": 1,
                "records": [
                    {
                        "name": "DOE, JANE",
                        "arrest_date": "2026-08-14",
                        "hold_reasons": "",
                        "is_juvenile": False,
                    }
                ],
            }
        )


class _TestZuercherScraper(ZuercherBaseScraper):
    @property
    def county(self):
        return "Test"

    @property
    def state(self):
        return "SC"

    @property
    def zuercher_domain(self):
        return "example.invalid"


class TestZuercherSafety(unittest.TestCase):
    def test_audited_south_carolina_wrappers_fail_closed_without_network(self):
        with patch("scrapers.zuercher_base.requests.Session", side_effect=AssertionError("network must not run")):
            for scraper_class in GUARDED_SCRAPERS:
                with self.subTest(scraper=scraper_class.__name__):
                    scraper = scraper_class()
                    self.assertFalse(scraper.SOURCE_CONTRACT_VALIDATED)
                    self.assertTrue(scraper.SOURCE_SAFETY_REASON)
                    self.assertEqual(scraper.scrape(), [])

    def test_missing_source_booking_fields_are_rejected_without_synthetic_key(self):
        with patch("scrapers.zuercher_base.requests.Session", return_value=_FakeSession()):
            self.assertEqual(_TestZuercherScraper().scrape(), [])

    def test_source_issued_booking_fields_are_preserved(self):
        class _MappedSession(_FakeSession):
            def post(self, *args, **kwargs):
                return _FakeResponse(
                    {
                        "total_record_count": 1,
                        "records": [
                            {
                                "name": "DOE, JANE",
                                "booking_number": "SC-1001",
                                "booking_date": "2026-08-14T12:34:00",
                                "hold_reasons": "",
                                "is_juvenile": False,
                            }
                        ],
                    }
                )

        with patch("scrapers.zuercher_base.requests.Session", return_value=_MappedSession()):
            records = _TestZuercherScraper().scrape()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].Booking_Number, "SC-1001")
        self.assertEqual(records[0].Booking_Date, "2026-08-14T12:34:00")
        self.assertEqual(records[0].Status, "Unknown")


if __name__ == "__main__":
    unittest.main()
