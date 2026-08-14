import unittest

from scrapers.counties_al.st_clair import StClairALScraper


CARD_HTML = """
<div class="col-lg-6">
  <div class="card-body">
    JANE Q DOE
    <strong>Booking #:</strong> SC-24590
    <strong>Age:</strong> 34
    <strong>Booking Date:</strong> 08-14-2026 2:35 PM
    <strong>Charges:</strong> Example Charge
    <strong>Bond:</strong> $1,000.00
    <a href="/inmate-roster/example">View Profile &gt;&gt;&gt;</a>
  </div>
</div>
"""


class TestStClairALScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = StClairALScraper()

    def test_maps_source_issued_booking_key_and_public_fields(self):
        records = self.scraper._parse_page(CARD_HTML)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.County, "St. Clair")
        self.assertEqual(record.State, "AL")
        self.assertEqual(record.Booking_Number, "SC-24590")
        self.assertEqual(record.Booking_Date, "08-14-2026 2:35 PM")
        self.assertEqual(record.First_Name, "Jane")
        self.assertEqual(record.Middle_Name, "Q")
        self.assertEqual(record.Last_Name, "Doe")
        self.assertEqual(record.Charges, "Example Charge")
        self.assertEqual(record.Bond_Amount, "$1,000.00")
        self.assertEqual(record.extra_data["booking_key_origin"], "source-issued public Booking #")
        self.assertEqual(record.Detail_URL, self.scraper.PORTAL_URL)
        self.assertEqual(record.Mugshot_URL, "")

    def test_missing_booking_number_or_date_fails_closed(self):
        missing_number = CARD_HTML.replace("SC-24590", "")
        missing_date = CARD_HTML.replace("08-14-2026 2:35 PM", "")
        self.assertEqual(self.scraper._parse_page(missing_number), [])
        self.assertEqual(self.scraper._parse_page(missing_date), [])

    def test_public_pagination_url_is_bounded_and_state_scoped(self):
        self.assertEqual(
            self.scraper._page_url(2),
            "https://www.stclairsheriff.org/inmate-roster/filters/current/booking_time=desc/2",
        )
        self.assertEqual(self.scraper.state, "AL")
        self.assertTrue(self.scraper._has_next_page('<a href="/inmate-roster/filters/current/booking_time=desc/2">Next</a>'))


if __name__ == "__main__":
    unittest.main()
