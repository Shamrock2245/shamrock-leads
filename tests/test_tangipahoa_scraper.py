import unittest

from scrapers.counties_la.tangipahoa import TangipahoaScraper


class TestTangipahoaScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = TangipahoaScraper()

    @staticmethod
    def _page(source_id="14650183", booking_date="08/14/2026 6:30 AM"):
        return f"""
        <table>
          <thead><tr><th>Name</th><th>DOB Race / Gender</th><th>Booking Date</th><th>Edit</th></tr></thead>
          <tbody>
            <tr>
              <td>DOE, JANE QUINN<br />{source_id}</td>
              <td>08/14/1990<br />W / F</td>
              <td>{booking_date}</td>
              <td><a href="/jail/TangipahoaJail/view/public-token">View</a></td>
            </tr>
          </tbody>
        </table>
        """

    def test_maps_official_table_row_with_labeled_surrogate(self):
        records = self.scraper._parse_page(self._page())

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.County, "Tangipahoa")
        self.assertEqual(record.State, "LA")
        self.assertEqual(record.Person_ID, "14650183")
        self.assertEqual(record.Booking_Number, "tangipahoa-public:14650183:08/14/2026-6:30-AM")
        self.assertEqual(record.Full_Name, "DOE, JANE QUINN")
        self.assertEqual(record.First_Name, "Jane")
        self.assertEqual(record.Middle_Name, "Quinn")
        self.assertEqual(record.Last_Name, "Doe")
        self.assertEqual(record.DOB, "08/14/1990")
        self.assertEqual(record.Race, "W")
        self.assertEqual(record.Sex, "F")
        self.assertEqual(record.Booking_Date, "08/14/2026 6:30 AM")
        self.assertEqual(record.Detail_URL, "https://tbs-web.com/jail/TangipahoaJail/view/public-token")
        self.assertEqual(record.extra_data["booking_key_origin"], "deterministic public roster ID + Booking Date; source does not label a booking number")

    def test_missing_or_non_numeric_source_id_fails_closed(self):
        self.assertEqual(self.scraper._parse_page(self._page(source_id="")), [])
        self.assertEqual(self.scraper._parse_page(self._page(source_id="not-a-source-id")), [])
        self.assertEqual(self.scraper._parse_page(self._page(booking_date="")), [])

    def test_page_count_uses_official_pagination_labels(self):
        html = '<a aria-label="Go to page 2">2</a><a aria-label="Go to page 73">73</a>'
        self.assertEqual(self.scraper._page_count(html), 73)

    def test_source_key_is_stable_for_same_public_source_values(self):
        first = self.scraper._surrogate_booking_key("14650183", "08/14/2026 6:30 AM")
        second = self.scraper._surrogate_booking_key("14650183", "08/14/2026 6:30 AM")
        later_booking = self.scraper._surrogate_booking_key("14650183", "08/15/2026 6:30 AM")
        self.assertEqual(first, second)
        self.assertNotEqual(first, later_booking)


if __name__ == "__main__":
    unittest.main()
