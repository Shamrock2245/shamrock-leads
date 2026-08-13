import unittest

from bs4 import BeautifulSoup

from scrapers.p2c_base import P2CBaseScraper


class TestP2CScraper(P2CBaseScraper):
    P2C_URL = "https://example.invalid/p2c/jailinmates.aspx"
    COUNTY_NAME = "Example"
    FACILITY_NAME = "Example Detention Center"

    @property
    def state(self):
        return "NC"


class P2CIdentityGuardTests(unittest.TestCase):
    def setUp(self):
        self.scraper = TestP2CScraper()

    def test_drops_row_without_source_booking_identifier(self):
        soup = BeautifulSoup(
            """
            <table>
              <tr><th>Name</th><th>Booking Date</th><th>Bond</th></tr>
              <tr><td>DOE, JANE</td><td>08/12/2026</td><td>$1,500.00</td></tr>
            </table>
            """,
            "html.parser",
        )

        self.assertEqual(self.scraper._parse(soup), [])

    def test_keeps_row_with_source_booking_identifier(self):
        soup = BeautifulSoup(
            """
            <table>
              <tr><th>Name</th><th>Booking</th><th>Booking Date</th><th>Bond</th></tr>
              <tr><td>DOE, JANE</td><td>123456</td><td>08/12/2026</td><td>$1,500.00</td></tr>
            </table>
            """,
            "html.parser",
        )

        records = self.scraper._parse(soup)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].Booking_Number, "123456")
        self.assertEqual(records[0].County, "Example")
        self.assertEqual(records[0].State, "NC")


if __name__ == "__main__":
    unittest.main()
