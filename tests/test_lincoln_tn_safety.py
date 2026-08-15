import unittest
from pathlib import Path

from scrapers.counties_tn.lincoln import LincolnTNScraper


class LincolnSafetyTests(unittest.TestCase):
    def test_fails_closed_without_network_or_records(self):
        scraper = LincolnTNScraper()
        self.assertFalse(scraper.SOURCE_CONTRACT_VALIDATED)
        self.assertEqual(scraper.scrape(), [])

    def test_no_generic_parser_invocation(self):
        source = Path('scrapers/counties_tn/lincoln.py').read_text(encoding='utf-8')
        self.assertNotIn('super().scrape()', source)


if __name__ == '__main__':
    unittest.main()
