import unittest
from pathlib import Path

from scrapers.counties_al.jefferson import JeffersonScraper


class JeffersonALSafetyTests(unittest.TestCase):
    def test_fails_closed_without_network_or_records(self):
        scraper = JeffersonScraper()
        self.assertFalse(scraper.SOURCE_CONTRACT_VALIDATED)
        self.assertEqual(scraper.scrape(), [])

    def test_no_prohibited_access_paths(self):
        source = Path('scrapers/counties_al/jefferson.py').read_text(encoding='utf-8').lower()
        for forbidden in ('proxy', 'stealth', 'newworld', 'curl_cffi'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
