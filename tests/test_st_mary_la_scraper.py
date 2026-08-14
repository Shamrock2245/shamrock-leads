import unittest

from scrapers.counties_la.st_mary import StMaryLARosterScraper


class TestStMaryLARosterScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = StMaryLARosterScraper()

    @staticmethod
    def _card(booking_number="LEC0020261021", booking_date="08-13-2026 5:23 PM"):
        return f"""
        <div class="col-lg-6">
          <div class="row">
            <div class="col-lg-8 inmate_data">
              <div>PRICE, CARTNEY J</div>
              <div class="inmate_data_bold"><strong>Booking #:</strong> {booking_number}</div>
              <div class="inmate_data_bold"><strong>Age:</strong> 37</div>
              <div class="inmate_data_bold"><strong>Booking Date:</strong> {booking_date}</div>
              <div class="inmate_data_bold"><strong>Charges:</strong> FAILURE TO APPEAR</div>
              <div class="inmate_data_bold"><strong>Bond:</strong> $2,000.00</div>
              <a href="/inmate-roster/public-token"><strong>View Profile &gt;&gt;&gt;</strong></a>
              <img src="/images/public.jpg" />
            </div>
          </div>
        </div>
        """

    def test_maps_official_card_with_source_issued_booking_number(self):
        records = self.scraper._parse_page(self._card())

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.County, "St. Mary")
        self.assertEqual(record.State, "LA")
        self.assertEqual(record.Booking_Number, "LEC0020261021")
        self.assertEqual(record.Full_Name, "PRICE, CARTNEY J")
        self.assertEqual(record.First_Name, "Cartney")
        self.assertEqual(record.Middle_Name, "J")
        self.assertEqual(record.Last_Name, "Price")
        self.assertEqual(record.Booking_Date, "08-13-2026 5:23 PM")
        self.assertEqual(record.Age_At_Arrest, "37")
        self.assertEqual(record.Charges, "FAILURE TO APPEAR")
        self.assertEqual(record.Bond_Amount, "$2,000.00")
        self.assertEqual(record.extra_data["booking_key_origin"], "source-issued public Booking #")

    def test_missing_source_issued_booking_number_or_date_fails_closed(self):
        self.assertEqual(self.scraper._parse_page(self._card(booking_number="")), [])
        self.assertEqual(self.scraper._parse_page(self._card(booking_date="")), [])

    def test_page_url_increments_only_terminal_page_number(self):
        self.assertEqual(
            self.scraper._page_url(4),
            "https://www.stmaryso.com/inmate-roster/filters/current/booking_time=desc/4",
        )

    def test_next_page_detection_is_explicit(self):
        self.assertTrue(self.scraper._has_next_page('<a href="/inmate-roster/filters/current/booking_time=desc/2">» Next</a>'))
        self.assertFalse(self.scraper._has_next_page('<a href="/inmate-roster/filters/current/booking_time=desc/1">1</a>'))


if __name__ == "__main__":
    unittest.main()
