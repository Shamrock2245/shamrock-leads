import unittest

from scrapers.counties_nc.durham import DurhamScraper


class TestDurhamScraperSafetyGuard(unittest.TestCase):
    def setUp(self):
        self.scraper = DurhamScraper()

    def test_preserves_state_scoped_identity(self):
        self.assertEqual(self.scraper.county, "Durham")
        self.assertEqual(self.scraper.state, "NC")
        self.assertEqual(
            self.scraper.roster_url,
            "https://www.durhamsheriff.com/community/public-information/inmate-population-search",
        )

    def test_unverified_source_contract_fails_closed_without_network(self):
        self.assertEqual(self.scraper.scrape(), [])


if __name__ == "__main__":
    unittest.main()
