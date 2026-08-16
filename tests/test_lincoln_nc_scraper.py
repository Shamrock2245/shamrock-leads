import unittest

from scrapers.counties_nc.lincoln import LincolnScraper


class TestLincolnNCScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = LincolnScraper()

    def test_declares_validated_official_ocv_contract(self):
        self.assertEqual(self.scraper.county, "Lincoln")
        self.assertEqual(self.scraper.state, "NC")
        self.assertEqual(self.scraper.app_id, "a46428092")
        self.assertEqual(
            self.scraper.portal_url,
            "https://www.lincolnsheriff.org/inmateSearch",
        )
        self.assertEqual(
            self.scraper.inmates_json_url,
            "https://myocv.s3.amazonaws.com/ocvapps/a46428092/inmates.json",
        )

    def test_maps_source_issued_inmate_id_and_booked_date(self):
        item = {
            "firstName": "JANE",
            "lastName": "DOE",
            "inmateID": "LC-1001",
            "content": "<div>Inmate ID: LC-1001</div><div>Booked Date: 08/14/2026 12:34</div>",
            "custody_status_cd": "IN",
            "chargeArray": [],
        }
        record = self.scraper._item_to_record(item)
        self.assertIsNotNone(record)
        self.assertEqual(record.Booking_Number, "LC-1001")
        self.assertEqual(record.Person_ID, "LC-1001")
        self.assertEqual(record.Booking_Date, "08/14/2026 12:34")
        self.assertEqual(record.Status, "In Custody")

    def test_missing_source_issued_identity_fails_closed(self):
        item = {
            "firstName": "JANE",
            "lastName": "DOE",
            "content": "<div>Booked Date: 08/14/2026 12:34</div>",
        }
        self.assertIsNone(self.scraper._item_to_record(item))

    def test_mongo_object_id_is_not_a_booking_number(self):
        item = {
            "firstName": "JANE",
            "lastName": "DOE",
            "_id": {"$id": "64f0deadbeef"},
            "content": "<div>Booked Date: 08/14/2026 12:34</div>",
        }
        self.assertIsNone(self.scraper._item_to_record(item))

    def test_missing_booked_date_fails_closed(self):
        item = {
            "firstName": "JANE",
            "lastName": "DOE",
            "inmateID": "LC-1001",
            "content": "<div>Inmate ID: LC-1001</div>",
        }
        self.assertIsNone(self.scraper._item_to_record(item))


if __name__ == "__main__":
    unittest.main()
