import inspect
import unittest

from scrapers.counties.sarasota import SarasotaCountyScraper


class TestSarasotaSafety(unittest.TestCase):
    def test_source_contract_is_explicitly_unverified(self):
        self.assertFalse(SarasotaCountyScraper.SOURCE_CONTRACT_VALIDATED)
        self.assertIn("official Sarasota", SarasotaCountyScraper.SOURCE_CONTRACT_REASON)

    def test_scrape_fails_closed_without_network(self):
        self.assertEqual(SarasotaCountyScraper().scrape(), [])

    def test_unsafe_source_and_sensitive_paths_are_absent(self):
        source = inspect.getsource(SarasotaCountyScraper)
        for prohibited in (
            "mugshots",
            "proxy",
            "captcha",
            "revize",
            "browser",
            "dob",
            "mugshot_url",
            "stealth",
            "requests",
        ):
            self.assertNotIn(prohibited, source.lower())


if __name__ == "__main__":
    unittest.main()
