import unittest

from scrapers.counties.broward import BrowardCountyScraper


class TestBrowardCountyScraperSafetyGuard(unittest.TestCase):
    def setUp(self):
        self.scraper = BrowardCountyScraper()

    def test_preserves_county_and_official_source_reference(self):
        self.assertEqual(self.scraper.county, "Broward")
        self.assertEqual(self.scraper.roster_url, "https://apps.sheriff.org/arrestsearch")

    def test_protected_source_fails_closed_without_network(self):
        self.assertEqual(self.scraper.scrape(), [])


if __name__ == "__main__":
    unittest.main()
