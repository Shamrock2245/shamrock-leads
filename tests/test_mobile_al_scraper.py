import inspect
import unittest

from scrapers.counties_al.mobile import MobileScraper


class TestMobileALScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = MobileScraper()

    def test_source_contract_is_explicitly_unverified(self):
        self.assertFalse(self.scraper.SOURCE_CONTRACT_VALIDATED)
        self.assertIn("booking-safe broad roster", self.scraper.SOURCE_CONTRACT_REASON)
        self.assertTrue(self.scraper.OFFICIAL_SOURCE_URL.startswith("https://"))

    def test_scrape_fails_closed_without_network(self):
        self.assertEqual(self.scraper.scrape(), [])

    def test_unsafe_legacy_patterns_are_absent(self):
        source = inspect.getsource(MobileScraper)
        self.assertNotIn("create_stealth_session", source)
        self.assertNotIn("prefer_residential", source)
        self.assertNotIn("MOB_", source)
        self.assertNotIn("DOB", source)


if __name__ == "__main__":
    unittest.main()
