import inspect
import unittest

from scrapers.counties_al.montgomery import MontgomeryScraper


class TestMontgomeryALScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = MontgomeryScraper()

    def test_source_contract_is_explicitly_unverified(self):
        self.assertFalse(self.scraper.SOURCE_CONTRACT_VALIDATED)
        self.assertIn("HTTP 403", self.scraper.SOURCE_CONTRACT_REASON)
        self.assertTrue(self.scraper.OFFICIAL_SOURCE_URL.startswith("https://"))

    def test_scrape_fails_closed_without_network(self):
        self.assertEqual(self.scraper.scrape(), [])

    def test_unverified_api_ingestion_is_absent(self):
        source = inspect.getsource(MontgomeryScraper)
        self.assertNotIn("requests.get", source)
        self.assertNotIn("resp.json", source)
        self.assertNotIn("scraped_at", source)


if __name__ == "__main__":
    unittest.main()
