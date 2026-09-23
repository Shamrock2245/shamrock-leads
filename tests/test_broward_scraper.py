import os
import unittest
from unittest import mock

from scrapers.counties.broward import BrowardCountyScraper


class TestBrowardCountyScraper(unittest.TestCase):
    def setUp(self):
        self.scraper = BrowardCountyScraper()

    def test_preserves_county_and_official_source_reference(self):
        self.assertEqual(self.scraper.county, "Broward")
        self.assertEqual(
            self.scraper.roster_url.lower(),
            "https://apps.sheriff.org/arrestsearch",
        )

    def test_source_contract_validated_after_write_smoke(self):
        self.assertTrue(self.scraper.SOURCE_CONTRACT_VALIDATED)

    def test_scrape_requires_solvecaptcha_key(self):
        with mock.patch.dict(os.environ, {"SOLVECAPTCHA_KEY": ""}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                self.scraper.scrape()
        self.assertIn("SOLVECAPTCHA_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
