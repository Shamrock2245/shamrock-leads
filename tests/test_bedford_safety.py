import unittest
from pathlib import Path

from scrapers.counties_tn.bedford import BedfordScraper


class BedfordSafetyTests(unittest.TestCase):
    def test_fails_closed_without_network_or_records(self):
        scraper = BedfordScraper()
        self.assertFalse(scraper.SOURCE_CONTRACT_VALIDATED)
        self.assertEqual(scraper.scrape(), [])

    def test_no_generic_parser_invocation(self):
        source = Path('scrapers/counties_tn/bedford.py').read_text(encoding='utf-8')
        self.assertNotIn('super().scrape()', source)


if __name__ == '__main__':
    unittest.main()
