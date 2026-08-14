import unittest

from scrapers.counties_al.marshall import MarshallALScraper


class TestMarshallALScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = MarshallALScraper()

    @staticmethod
    def _card(booking_number="24876", booking_date="08-14-2026 4:54 AM"):
        return f"""
        <div class="col-lg-6">
          <div class="row">
            <div class="col-lg-8 inmate_data">
              <div>DOE, JANE QUINN</div>
              <div class="inmate_data_bold"><strong>Booking #:</strong> {booking_number}</div>
              <div class="inmate_data_bold"><strong>Age:</strong> 31</div>
              <div class="inmate_data_bold"><strong>Booking Date:</strong> {booking_date}</div>
              <div class="inmate_data_bold"><strong>Charges:</strong> EXAMPLE CHARGE</div>
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
        self.assertEqual(record.County, "Marshall")
        self.assertEqual(record.State, "AL")
        self.assertEqual(record.Booking_Number, "24876")
        self.assertEqual(record.Full_Name, "DOE, JANE QUINN")
        self.assertEqual(record.First_Name, "Jane")
        self.assertEqual(record.Middle_Name, "Quinn")
        self.assertEqual(record.Last_Name, "Doe")
        self.assertEqual(record.Booking_Date, "08-14-2026 4:54 AM")
        self.assertEqual(record.Age_At_Arrest, "31")
        self.assertEqual(record.Charges, "EXAMPLE CHARGE")
        self.assertEqual(record.Detail_URL, "https://www.marshallso.org/inmate-roster/public-token")
        self.assertEqual(record.extra_data["booking_key_origin"], "source-issued public Booking #")

    def test_missing_source_issued_booking_number_or_date_fails_closed(self):
        self.assertEqual(self.scraper._parse_page(self._card(booking_number="")), [])
        self.assertEqual(self.scraper._parse_page(self._card(booking_date="")), [])

    def test_page_url_increments_only_the_terminal_page_number(self):
        self.assertEqual(
            self.scraper._page_url(4),
            "https://www.marshallso.org/inmate-roster/filters/current/booking_time=desc/4",
        )

    def test_next_page_detection_is_explicit(self):
        self.assertTrue(self.scraper._has_next_page('<a href="/inmate-roster/filters/current/booking_time=desc/2">» Next</a>'))
        self.assertFalse(self.scraper._has_next_page('<a href="/inmate-roster/filters/current/booking_time=desc/1">1</a>'))


if __name__ == "__main__":
    unittest.main()
