import inspect
import unittest

from scrapers.counties_al.tuscaloosa import TuscaloosaScraper


class TestTuscaloosaSafety(unittest.TestCase):
    def test_source_contract_is_explicitly_unverified(self):
        self.assertFalse(TuscaloosaScraper.SOURCE_CONTRACT_VALIDATED)
        self.assertIn("human verification", TuscaloosaScraper.SOURCE_CONTRACT_REASON)

    def test_scrape_fails_closed_without_network(self):
        self.assertEqual(TuscaloosaScraper().scrape(), [])

    def test_wrong_jurisdiction_and_unsafe_parser_paths_are_absent(self):
        source = inspect.getsource(TuscaloosaScraper).lower()
        for prohibited in (
            "requests",
            "proxy",
            "booking_number",
            "item.get",
            "tcso.org/api",
            "tulsa",
        ):
            self.assertNotIn(prohibited, source)

        self.assertEqual(
            TuscaloosaScraper.OFFICIAL_SOURCE_URL,
            "https://www.tcsoal.org/inmates",
        )


if __name__ == "__main__":
    unittest.main()
