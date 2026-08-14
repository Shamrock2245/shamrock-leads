import unittest

from scrapers.counties_tx.randall import RandallScraper


class TestRandallScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = RandallScraper()

    @staticmethod
    def _card(name="DOE, JANE QUINN", inmate_id="R-1001", booking_date="06:30:00 08/14/2026"):
        return f"""
        <div class="bg-content1">
          <a href="/inmateSearch/public-detail-token"><h2>{name}</h2></a>
          <div class="render-html body_md">
            <p><b>Inmate Information:</b></p>
            <p>Booking Date: {booking_date}</p>
            <p>Gender: F</p>
            <p>Race: W</p>
            <p>Age: 31</p>
            <p>Height: 5'05&quot;</p>
            <p>Weight: 140</p>
            <p>Inmate ID: {inmate_id}</p>
            <p>Arresting Agency: RCSO</p>
            <p>Cause Number: TEST-2026-001</p>
            <p><b>Charge(s):</b></p>
            <p>Description: EXAMPLE CHARGE ONE</p>
            <p>Bond Amount Required : $1,500</p>
            <p>Description: EXAMPLE CHARGE TWO</p>
            <p>Bond Amount Required : $250</p>
            <img src="https://cdn.example.test/public.jpg" />
          </div>
        </div>
        """

    def test_maps_official_card_and_labels_surrogate_key(self):
        records = self.scraper._parse_page(self._card())

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.County, "Randall")
        self.assertEqual(record.State, "TX")
        self.assertEqual(record.Booking_Number, "randall-public:R-1001:06:30:00-08/14/2026")
        self.assertEqual(record.Person_ID, "R-1001")
        self.assertEqual(record.Full_Name, "DOE, JANE QUINN")
        self.assertEqual(record.First_Name, "Jane")
        self.assertEqual(record.Middle_Name, "Quinn")
        self.assertEqual(record.Last_Name, "Doe")
        self.assertEqual(record.Booking_Date, "06:30:00 08/14/2026")
        self.assertEqual(record.Bond_Amount, "1750.00")
        self.assertEqual(record.Charges, "EXAMPLE CHARGE ONE | EXAMPLE CHARGE TWO")
        self.assertEqual(record.Case_Number, "TEST-2026-001")
        self.assertEqual(record.Status, "In Custody")
        self.assertEqual(record.extra_data["booking_key_origin"], "deterministic public Inmate ID + Booking Date; source does not label a booking number")
        self.assertEqual(record.Detail_URL, "https://www.randallso.gov/inmateSearch/public-detail-token")

    def test_missing_source_id_or_booking_date_fails_closed(self):
        self.assertEqual(self.scraper._parse_page(self._card(inmate_id="")), [])
        self.assertEqual(self.scraper._parse_page(self._card(booking_date="")), [])

    def test_page_count_uses_official_pagination_labels(self):
        html = '<button aria-label="Go to page 1">1</button><button aria-label="Go to page 84">84</button>'
        self.assertEqual(self.scraper._page_count(html), 84)

    def test_source_key_is_stable_for_same_public_source_values(self):
        one = self.scraper._surrogate_booking_key("R-1001", "06:30:00 08/14/2026")
        two = self.scraper._surrogate_booking_key("R-1001", "06:30:00 08/14/2026")
        different_booking = self.scraper._surrogate_booking_key("R-1001", "07:30:00 08/14/2026")
        self.assertEqual(one, two)
        self.assertNotEqual(one, different_booking)


if __name__ == "__main__":
    unittest.main()
