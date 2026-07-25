"""
Unit tests for Sarasota County Arrest Scraper (scrapers/counties/sarasota.py).
"""
import unittest
from scrapers.counties.sarasota import SarasotaCountyScraper


class TestSarasotaCountyScraper(unittest.TestCase):

    def setUp(self):
        self.scraper = SarasotaCountyScraper()

    def test_city_slug_cleaning(self):
        sample_post = {
            "id": 12345,
            "date": "2026-07-25T14:30:00",
            "title": {"rendered": "SHAWN STOUT"},
            "slug": "shawn-stout-of-north-port-2",
            "link": "https://mugshotssarasota.com/2026/07/25/shawn-stout-of-north-port-2/",
            "yoast_head_json": {
                "description": "SHAWN STOUT - age41 arrested on 20260725 for MOVING TRAFFIC VIOL: OPERATE MOTORCYCLE W/O LICENSE. Bail $2000.00.",
                "og_image": [{"url": "https://mugshotssarasota.com/wp-content/uploads/sites/15/2026/07/SHAWN-STOUT-202600007046-s13.jpg"}]
            }
        }
        record = self.scraper._parse_mugshots_post(sample_post)
        self.assertIsNotNone(record)
        self.assertEqual(record.County, "Sarasota")
        self.assertEqual(record.State, "FL")
        self.assertEqual(record.Booking_Number, "202600007046")
        self.assertEqual(record.Full_Name, "STOUT, SHAWN")
        self.assertEqual(record.First_Name, "SHAWN")
        self.assertEqual(record.Last_Name, "STOUT")
        self.assertEqual(record.Age_At_Arrest, "41")
        self.assertEqual(record.Arrest_Date, "2026-07-25")
        self.assertEqual(record.City, "North Port")
        self.assertEqual(record.Bond_Amount, "2000")
        self.assertEqual(record.Bond_Type, "Surety")

    def test_split_bond_no_bond(self):
        amt, b_type = self.scraper._split_bond("Bail No Bond")
        self.assertEqual(amt, "0")
        self.assertEqual(b_type, "No Bond")

    def test_split_bond_amount(self):
        amt, b_type = self.scraper._split_bond("$2,500.00")
        self.assertEqual(amt, "2500")
        self.assertEqual(b_type, "Surety")


if __name__ == "__main__":
    unittest.main()
