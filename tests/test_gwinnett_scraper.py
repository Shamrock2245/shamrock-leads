import unittest

from scrapers.counties_ga.gwinnett import GwinnettScraper


class TestGwinnettScraperSafetyGuard(unittest.TestCase):
    def setUp(self):
        self.scraper = GwinnettScraper()

    def test_preserves_state_scoped_identity(self):
        self.assertEqual(self.scraper.county, "Gwinnett")
        self.assertEqual(self.scraper.state, "GA")
        self.assertEqual(
            self.scraper.roster_url,
            "https://www.gwinnettcountysheriff.com/smartwebclient/",
        )

    def test_incomplete_bulk_identity_fails_closed_without_network(self):
        self.assertEqual(self.scraper.scrape(), [])


if __name__ == "__main__":
    unittest.main()
