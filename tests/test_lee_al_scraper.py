import unittest

from scrapers.counties_al.lee import LeeALScraper


class TestLeeALScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = LeeALScraper()

    @staticmethod
    def _card(name="DOE, JANE QUINN", name_id="L-1001", booking_date="08/14/26 6:30 AM"):
        return f"""
        <div class="bg-content1">
          <a href="/inmateSearch/public-detail-token"><h2>{name}</h2></a>
          <div class="render-html body_md">
            <p>Booking Date: {booking_date}</p>
            <p>NameID: {name_id}</p>
            <p>Age: 31</p>
            <p>Race: W</p>
            <p>Sex: F</p>
            <p><b>Charge(s):</b></p>
            <p>Description: EXAMPLE CHARGE ONE</p>
            <p>Bond Amount: $1,500</p>
            <p>Description: EXAMPLE CHARGE TWO</p>
            <p>Bond Amount: $250</p>
            <img src="https://cdn.example.test/public.jpg" />
          </div>
        </div>
        """

    def test_maps_official_card_and_labels_state_scoped_surrogate(self):
        records = self.scraper._parse_page(self._card())

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.County, "Lee")
        self.assertEqual(record.State, "AL")
        self.assertEqual(record.Booking_Number, "lee-al-public:L-1001:08/14/26-6:30-AM")
        self.assertEqual(record.Person_ID, "L-1001")
        self.assertEqual(record.Full_Name, "DOE, JANE QUINN")
        self.assertEqual(record.First_Name, "Jane")
        self.assertEqual(record.Middle_Name, "Quinn")
        self.assertEqual(record.Last_Name, "Doe")
        self.assertEqual(record.Booking_Date, "08/14/26 6:30 AM")
        self.assertEqual(record.Bond_Amount, "1750.00")
        self.assertEqual(record.Charges, "EXAMPLE CHARGE ONE | EXAMPLE CHARGE TWO")
        self.assertEqual(record.Status, "In Custody")
        self.assertEqual(record.extra_data["booking_key_origin"], "deterministic public NameID + Booking Date; source does not label a booking number")
        self.assertEqual(record.Detail_URL, "https://www.leecosheriffal.gov/inmateSearch/public-detail-token")

    def test_missing_name_id_or_booking_date_fails_closed(self):
        self.assertEqual(self.scraper._parse_page(self._card(name_id="")), [])
        self.assertEqual(self.scraper._parse_page(self._card(booking_date="")), [])

    def test_page_count_uses_official_pagination_labels(self):
        html = '<button aria-label="Go to page 1">1</button><button aria-label="Go to page 9">9</button>'
        self.assertEqual(self.scraper._page_count(html), 9)

    def test_source_key_is_stable_and_distinct_from_other_lee_states(self):
        first = self.scraper._surrogate_booking_key("L-1001", "08/14/26 6:30 AM")
        second = self.scraper._surrogate_booking_key("L-1001", "08/14/26 6:30 AM")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("lee-al-public:"))
        self.assertNotIn("sc", first)
        self.assertNotIn("ga", first)
        self.assertNotIn("nc", first)


if __name__ == "__main__":
    unittest.main()
