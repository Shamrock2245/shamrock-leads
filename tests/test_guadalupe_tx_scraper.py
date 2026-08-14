import unittest

from scrapers.counties_tx.guadalupe import GuadalupeScraper


class TestGuadalupeScraperSafetyGuard(unittest.TestCase):
    def setUp(self):
        self.scraper = GuadalupeScraper()

    def test_preserves_texas_scoped_identity_and_official_source(self):
        self.assertEqual(self.scraper.county, "Guadalupe")
        self.assertEqual(self.scraper.state, "TX")
        self.assertEqual(
            self.scraper.roster_url,
            "https://portal-txguadalupe.tylertech.cloud/PublicAccess/JailingSearch.aspx?ID=600",
        )

    def test_human_verification_source_fails_closed_without_network(self):
        self.assertEqual(self.scraper.scrape(), [])


if __name__ == "__main__":
    unittest.main()
