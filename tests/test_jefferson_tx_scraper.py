import unittest

from scrapers.counties_tx.jefferson import JeffersonScraper


class TestJeffersonScraperSafetyGuard(unittest.TestCase):
    def setUp(self):
        self.scraper = JeffersonScraper()

    def test_preserves_texas_scoped_identity_and_official_source(self):
        self.assertEqual(self.scraper.county, "Jefferson")
        self.assertEqual(self.scraper.state, "TX")
        self.assertEqual(
            self.scraper.roster_url,
            "https://www.sheriff.jeffersoncountytx.gov/inmateSearch",
        )

    def test_unverified_bulk_contract_fails_closed_without_network(self):
        self.assertEqual(self.scraper.scrape(), [])


if __name__ == "__main__":
    unittest.main()
