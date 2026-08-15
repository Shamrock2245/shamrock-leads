import unittest
from pathlib import Path

from scrapers.counties_ms.jackson import JacksonScraper


class JacksonSafetyTests(unittest.TestCase):
    def test_fails_closed_without_network_or_records(self):
        scraper = JacksonScraper()
        self.assertFalse(scraper.SOURCE_CONTRACT_VALIDATED)
        self.assertEqual(scraper.scrape(), [])

    def test_no_prohibited_access_or_identity_paths(self):
        source = Path('scrapers/counties_ms/jackson.py').read_text(encoding='utf-8')
        for forbidden in ('proxy', 'stealth', 'captcha', 'DOB', 'hashlib', 'requests'):
            self.assertNotIn(forbidden, source.lower())


if __name__ == '__main__':
    unittest.main()
