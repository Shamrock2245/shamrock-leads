import unittest

from scrapers.counties_ct.ct_doc import CTDOCInmateScraper


class TestCTDOCInmateScraperSafetyGuard(unittest.TestCase):
    def setUp(self):
        self.scraper = CTDOCInmateScraper()

    def test_preserves_statewide_identity_and_registration_id(self):
        self.assertEqual(self.scraper.county, "CT DOC")
        self.assertEqual(self.scraper.state, "CT")
        self.assertEqual(self.scraper.scraper_id, "scraper_ct_doc")
        self.assertEqual(
            self.scraper.roster_url,
            "https://www.ctinmateinfo.state.ct.us/searchop.asp",
        )

    def test_access_rejected_source_fails_closed_without_network(self):
        self.assertEqual(self.scraper.scrape(), [])


if __name__ == "__main__":
    unittest.main()
