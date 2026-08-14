import unittest

from scrapers.counties_tx.ellis import EllisScraper


class TestEllisScraperSafetyGuard(unittest.TestCase):
    def setUp(self):
        self.scraper = EllisScraper()

    def test_preserves_texas_scoped_identity_and_official_source(self):
        self.assertEqual(self.scraper.county, "Ellis")
        self.assertEqual(self.scraper.state, "TX")
        self.assertEqual(self.scraper.roster_url, "https://ecso.llhostings.com/")

    def test_unverified_challenged_source_fails_closed_without_network(self):
        self.assertEqual(self.scraper.scrape(), [])


if __name__ == "__main__":
    unittest.main()
