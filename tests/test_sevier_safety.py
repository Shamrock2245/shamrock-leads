import unittest
from pathlib import Path
import requests

from scrapers.counties_tn.sevier import SevierScraper


class SevierSafetyTests(unittest.TestCase):
    def test_fails_closed_without_network_or_records(self):
        scraper = SevierScraper()
        self.assertTrue(scraper.SOURCE_CONTRACT_VALIDATED)
        with unittest.mock.patch('requests.Session.get', side_effect=requests.RequestException('network_down')):
            self.assertEqual(scraper.scrape(), [])

    def test_no_stale_base_parser_invocation(self):
        source = Path('scrapers/counties_tn/sevier.py').read_text(encoding='utf-8')
        self.assertNotIn('super().scrape()', source)


if __name__ == '__main__':
    unittest.main()
