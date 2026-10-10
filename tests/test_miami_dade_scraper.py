import unittest
from datetime import datetime, timezone

from scrapers.counties.miami_dade import MiamiDadeCountyScraper, OUT_FIELDS


class TestMiamiDadeCountyScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = MiamiDadeCountyScraper()
        self.book_date_ms = int(datetime(2026, 8, 14, tzinfo=timezone.utc).timestamp() * 1000)

    def test_maps_minimal_official_arcgis_fields(self):
        record = self.scraper._parse_record(
            {
                "GlobalID": "public-global-id",
                "ObjectId": 123,
                "BookDate": self.book_date_ms,
                "Defendant": "DOE, JANE Q",
                "Charge1": "EXAMPLE CHARGE",
            }
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.County, "Miami-Dade")
        self.assertEqual(record.State, "FL")
        # 2026-10-09: no source booking number; row ids are never used as keys.
        self.assertEqual(record.Booking_Number, "")
        self.assertEqual(record.Full_Name, "DOE, JANE Q")
        self.assertEqual(record.First_Name, "JANE")
        self.assertEqual(record.Last_Name, "DOE")
        self.assertEqual(record.Booking_Date, "2026-08-14")
        self.assertEqual(record.Status, "Unknown")
        self.assertEqual(record.extra_data["booking_key_origin"], "none: the source publishes no booking number")
        self.assertTrue(record.extra_data["md_dedupe"].startswith("md_dedupe_v2:"))
        self.assertIs(record.extra_data["md_key_fallback"], True)  # no DOB in this row
        self.assertEqual(record.Bond_Amount, "")  # no bond field: unknown, never $0
        self.assertIs(record.extra_data["bond_published"], False)

    def test_row_ids_never_become_the_booking_number(self):
        record = self.scraper._parse_record(
            {
                "ObjectId": 456,
                "BookDate": self.book_date_ms,
                "Defendant": "DOE, JOHN",
            }
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.Booking_Number, "")
        self.assertNotIn("456", str(record.to_mongo_doc().get("booking_number")))

    def test_missing_complete_identity_or_booking_boundary_fails_closed(self):
        base = {"GlobalID": "public-id", "BookDate": self.book_date_ms, "Defendant": "DOE, JANE"}
        self.assertIsNone(self.scraper._parse_record({**base, "Defendant": "DOE"}))
        self.assertIsNone(self.scraper._parse_record({**base, "Defendant": ""}))
        self.assertIsNone(self.scraper._parse_record({**base, "BookDate": None}))

    def test_query_field_list_excludes_address(self):
        self.assertNotIn("Address", OUT_FIELDS)
        self.assertNotIn("Zip", OUT_FIELDS)
        # 2026-10-09 owner exception: DOB is part of the internal natural key.
        self.assertIn("DOB", OUT_FIELDS)
        self.assertNotIn("GlobalID", OUT_FIELDS)  # reissued on republish; not fetched
        self.assertIn("BookDate", OUT_FIELDS)
        self.assertIn("Defendant", OUT_FIELDS)


if __name__ == "__main__":
    unittest.main()
