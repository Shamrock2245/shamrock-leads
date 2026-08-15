import unittest
from pathlib import Path

from scrapers.counties_tn.giles import GilesScraper


class GilesSafetyTests(unittest.TestCase):
    def test_fails_closed_without_network_or_records(self):
        scraper = GilesScraper()
        self.assertFalse(scraper.SOURCE_CONTRACT_VALIDATED)
        self.assertEqual(scraper.scrape(), [])

    def test_no_generic_parser_invocation(self):
        source = Path('scrapers/counties_tn/giles.py').read_text(encoding='utf-8')
        self.assertNotIn('super().scrape()', source)


if __name__ == '__main__':
    unittest.main()
