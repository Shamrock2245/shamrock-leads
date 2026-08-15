import unittest
from pathlib import Path

from scrapers.counties_tn.sevier import SevierScraper


class SevierSafetyTests(unittest.TestCase):
    def test_fails_closed_without_network_or_records(self):
        scraper = SevierScraper()
        self.assertFalse(scraper.SOURCE_CONTRACT_VALIDATED)
        self.assertEqual(scraper.scrape(), [])

    def test_no_stale_base_parser_invocation(self):
        source = Path('scrapers/counties_tn/sevier.py').read_text(encoding='utf-8')
        self.assertNotIn('super().scrape()', source)


if __name__ == '__main__':
    unittest.main()
