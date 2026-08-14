import unittest
from unittest.mock import patch

from scrapers.base_scraper import BaseScraper
from scrapers.counties_nc.buncombe import BuncombeScraper
from scrapers.counties_nc.johnston import JohnstonScraper


class SourceBookingIdentityGuardTests(unittest.TestCase):
    def test_rejects_known_synthetic_booking_patterns(self):
        synthetic_values = [
            "AIK_0123456789",
            "SC_abcdef1234",
            "SC_TEST_12345",
            "ONS_TESTPERSON",
            "CAT_0123456789ab",
            "RAN_0123456789ab",
        ]
        for value in synthetic_values:
            with self.subTest(value=value):
                self.assertFalse(BaseScraper._has_source_booking_identifier(value))

        self.assertTrue(BaseScraper._has_source_booking_identifier("202600123"))
        self.assertTrue(BaseScraper._has_source_booking_identifier("ABC-42-2026"))

    def test_buncombe_requires_numeric_source_booking_value(self):
        scraper = BuncombeScraper()
        without_booking = """
        <table><tr><th>Name</th></tr>
        <tr><td>TEST, PERSON</td><td>Charge Label</td></tr>
        </table>
        """
        with_booking = """
        <table><tr><th>Name</th></tr>
        <tr><td>TEST, PERSON</td><td>123456</td></tr>
        </table>
        """

        self.assertEqual(scraper._parse_html(without_booking, set()), [])
        records = scraper._parse_html(with_booking, set())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].Booking_Number, "123456")

    def test_johnston_requires_source_linked_nameid(self):
        class FakeResponse:
            def __init__(self, html):
                self.text = html

            def raise_for_status(self):
                return None

        class FakeSession:
            def __init__(self, html):
                self.headers = {}
                self._html = html

            def get(self, *_args, **_kwargs):
                return FakeResponse(self._html)

        no_identifier = """
        <table><tr><td>TEST, PERSON</td><td>Charge</td><td>08/12/2026</td></tr></table>
        """
        source_identifier = """
        <table><tr><td><a href='b_jailsearch3.cfm?nameid=42'>TEST, PERSON</a></td>
        <td>Charge</td><td>08/12/2026</td></tr></table>
        """

        with patch(
            "scrapers.counties_nc.johnston.requests.Session",
            side_effect=lambda: FakeSession(no_identifier),
        ):
            self.assertEqual(JohnstonScraper().scrape(), [])

        with patch(
            "scrapers.counties_nc.johnston.requests.Session",
            side_effect=lambda: FakeSession(source_identifier),
        ):
            records = JohnstonScraper().scrape()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].Booking_Number, "42")


if __name__ == "__main__":
    unittest.main()
