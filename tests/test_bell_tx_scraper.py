import unittest

from scrapers.counties_tx.bell import BellScraper


class TestBellScraperSafetyGuard(unittest.TestCase):
    def setUp(self):
        self.scraper = BellScraper()

    def test_preserves_texas_scoped_identity_and_official_source(self):
        self.assertEqual(self.scraper.county, "Bell")
        self.assertEqual(self.scraper.state, "TX")
        self.assertEqual(
            self.scraper.roster_url,
            "https://nwweb.bellcounty.texas.gov/NewWorld.InmateInquiry/TX0140000",
        )

    def test_unverified_broad_list_contract_fails_closed_without_network(self):
        self.assertEqual(self.scraper.scrape(), [])


if __name__ == "__main__":
    unittest.main()
