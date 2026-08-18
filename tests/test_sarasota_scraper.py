"""
Unit tests for Sarasota County Arrest Scraper (scrapers/counties/sarasota.py).
"""
import unittest
from scrapers.counties.sarasota import SarasotaCountyScraper


class TestSarasotaCountyScraper(unittest.TestCase):

    def setUp(self):
        self.scraper = SarasotaCountyScraper()

    def test_sarasota_county_properties(self):
        self.assertEqual(self.scraper.county, "Sarasota")
        self.assertEqual(self.scraper.state, "FL")
        self.assertFalse(self.scraper.SOURCE_CONTRACT_VALIDATED)

    def test_sarasota_fails_closed_returns_empty_list(self):
        records = self.scraper.scrape()
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main()

