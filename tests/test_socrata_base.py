import unittest

from scrapers.socrata_base import SocrataBaseScraper


class TestSocrataBaseScraper(unittest.TestCase):
    def test_maps_only_source_issued_identity_and_booking_fields(self):
        record = SocrataBaseScraper._record_from_item(
            {
                "first_name": "Jordan",
                "last_name": "Smith",
                "booking_number": "BK-12345",
                "booking_date": "2026-08-14",
                "charge": "PUBLIC CHARGE",
                "bond_amount": "$1,250.00",
            },
            "Fulton",
            "GA",
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.County, "Fulton")
        self.assertEqual(record.State, "GA")
        self.assertEqual(record.Full_Name, "Smith, Jordan")
        self.assertEqual(record.Booking_Number, "BK-12345")
        self.assertEqual(record.Booking_Date, "2026-08-14")
        self.assertEqual(record.Bond_Amount, "1250.00")
        self.assertEqual(record.Status, "Unknown")
        self.assertEqual(record.extra_data["booking_key_origin"], "source-issued public booking_number")

    def test_accepts_source_so_id_when_booking_number_is_absent(self):
        record = SocrataBaseScraper._record_from_item(
            {
                "name": "Jordan Smith",
                "so_id": "SOURCE-7",
                "arrest_date": "2026-08-14",
            },
            "Fulton",
            "GA",
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.Booking_Number, "SOURCE-7")
        self.assertEqual(record.extra_data["booking_key_origin"], "source-issued public so_id")

    def test_incomplete_identity_or_booking_boundary_fails_closed(self):
        complete = {
            "first_name": "Jordan",
            "last_name": "Smith",
            "booking_number": "BK-12345",
            "booking_date": "2026-08-14",
        }
        incomplete_name = {**complete, "first_name": "J"}
        missing_number = {key: value for key, value in complete.items() if key != "booking_number"}
        missing_date = {key: value for key, value in complete.items() if key != "booking_date"}

        self.assertIsNone(SocrataBaseScraper._record_from_item(incomplete_name, "Fulton", "GA"))
        self.assertIsNone(SocrataBaseScraper._record_from_item(missing_number, "Fulton", "GA"))
        self.assertIsNone(SocrataBaseScraper._record_from_item(missing_date, "Fulton", "GA"))


if __name__ == "__main__":
    unittest.main()
