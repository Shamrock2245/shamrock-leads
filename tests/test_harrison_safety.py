import unittest
from pathlib import Path

from scrapers.counties_ms.harrison import HarrisonScraper


class HarrisonSafetyTests(unittest.TestCase):
    def test_fails_closed_without_network_or_records(self):
        scraper = HarrisonScraper()
        self.assertFalse(scraper.SOURCE_CONTRACT_VALIDATED)
        self.assertEqual(scraper.scrape(), [])

    def test_no_opaque_api_or_identity_fallback(self):
        source = Path('scrapers/counties_ms/harrison.py').read_text(encoding='utf-8')
        for forbidden in ('api/inmates/roster', 'booking_number', 'item.get', 'requests.get'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
