import unittest
from pathlib import Path

from scrapers.counties_ms.hinds import HindsScraper


class HindsSafetyTests(unittest.TestCase):
    def test_fails_closed_without_network_or_records(self):
        scraper = HindsScraper()
        self.assertFalse(scraper.SOURCE_CONTRACT_VALIDATED)
        self.assertEqual(scraper.scrape(), [])

    def test_no_legacy_sensitive_or_profile_paths(self):
        source = Path('scrapers/counties_ms/hinds.py').read_text(encoding='utf-8')
        for forbidden in ('inmate_detail', 'Address', 'DOB=', 'requests.Session'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
