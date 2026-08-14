import unittest

from scrapers.counties_al.etowah import EtowahScraper


ROSTER_HTML = """
<div class="column medium-6 inmate_div">
  <h3>DOE, JANE Q</h3>
  <strong>Booking #:</strong> ECSO26JBN000123
  <strong>Age:</strong> 31
  <strong>Booking Date:</strong> 08-14-2026 - 11:54 am
  <strong>Charges:</strong><br/>
  SAMPLE CHARGE ONE<br/>
  SAMPLE CHARGE TWO<br/>
  <strong>Bond:</strong> $1,250.00
  <a href="roster_view.php?booking_num=ECSO26JBN000123">View Profile &gt;&gt;&gt;</a>
</div>
<a href="roster.php?&grp=10">&gt;</a>
"""


class TestEtowahALScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = EtowahScraper()

    def test_maps_source_issued_booking_key_and_public_fields(self):
        records = self.scraper._parse_page(ROSTER_HTML)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.County, "Etowah")
        self.assertEqual(record.State, "AL")
        self.assertEqual(record.Booking_Number, "ECSO26JBN000123")
        self.assertEqual(record.Booking_Date, "08-14-2026 - 11:54 am")
        self.assertEqual(record.First_Name, "Jane")
        self.assertEqual(record.Middle_Name, "Q")
        self.assertEqual(record.Last_Name, "Doe")
        self.assertEqual(record.Charges, "SAMPLE CHARGE ONE | SAMPLE CHARGE TWO")
        self.assertEqual(record.Bond_Amount, "1250.00")
        self.assertEqual(record.extra_data["booking_key_origin"], "source-issued public Booking #")

    def test_missing_booking_number_or_date_fails_closed(self):
        without_number = ROSTER_HTML.replace("ECSO26JBN000123", "")
        without_date = ROSTER_HTML.replace("08-14-2026 - 11:54 am", "")
        self.assertEqual(self.scraper._parse_page(without_number), [])
        self.assertEqual(self.scraper._parse_page(without_date), [])

    def test_public_pagination_is_bounded_and_state_scoped(self):
        self.assertEqual(self.scraper._page_url(0), self.scraper.ROSTER_URL)
        self.assertEqual(self.scraper._page_url(1), f"{self.scraper.ROSTER_URL}?grp=20")
        self.assertTrue(self.scraper._has_next_page(ROSTER_HTML))


if __name__ == "__main__":
    unittest.main()
