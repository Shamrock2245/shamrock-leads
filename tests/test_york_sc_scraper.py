import unittest

from bs4 import BeautifulSoup

from scrapers.counties_sc.york import YorkScraper


class TestYorkSCScraper(unittest.TestCase):
    @staticmethod
    def _card(booking_number="YORK-2026-123", booking_date="08/14/2026 09:20 AM"):
        return f"""
        <table class="table2">
          <tr><td>Doe, Jane Marie</td></tr>
          <tr>
            <td>Booking Number</td><td>{booking_number}</td>
            <td>Booking Date</td><td>{booking_date}</td>
          </tr>
          <tr>
            <td>Bond</td><td>$1,250.00</td>
            <td>York County Detention Center</td><td></td>
          </tr>
          <tr><td>
            <table>
              <tr><td>Sequence#</td><td>Charge Description</td><td>Arresting Agency</td></tr>
              <tr><td>1</td><td>PUBLIC CHARGE ONE</td><td>PUBLIC AGENCY</td></tr>
              <tr><td>2</td><td>PUBLIC CHARGE TWO</td><td>PUBLIC AGENCY</td></tr>
            </table>
          </td></tr>
        </table>
        """

    def test_maps_official_card_with_source_issued_booking_number(self):
        card = BeautifulSoup(self._card(), "html.parser").find("table")
        record = YorkScraper._record_from_card(card)

        self.assertIsNotNone(record)
        self.assertEqual(record.County, "York")
        self.assertEqual(record.State, "SC")
        self.assertEqual(record.Full_Name, "Doe, Jane Marie")
        self.assertEqual(record.First_Name, "Jane")
        self.assertEqual(record.Middle_Name, "Marie")
        self.assertEqual(record.Last_Name, "Doe")
        self.assertEqual(record.Booking_Number, "YORK-2026-123")
        self.assertEqual(record.Booking_Date, "08/14/2026 09:20 AM")
        self.assertEqual(record.Bond_Amount, "1250.00")
        self.assertEqual(record.Charges, "PUBLIC CHARGE ONE | PUBLIC CHARGE TWO")
        self.assertEqual(record.Status, "Unknown")
        self.assertEqual(record.extra_data["booking_key_origin"], "source-issued public Booking Number")

    def test_missing_source_issued_booking_number_or_date_fails_closed(self):
        missing_number = BeautifulSoup(self._card(booking_number=""), "html.parser").find("table")
        missing_date = BeautifulSoup(self._card(booking_date=""), "html.parser").find("table")
        self.assertIsNone(YorkScraper._record_from_card(missing_number))
        self.assertIsNone(YorkScraper._record_from_card(missing_date))

    def test_public_roster_url_is_https(self):
        self.assertTrue(YorkScraper().roster_url.startswith("https://"))


if __name__ == "__main__":
    unittest.main()
