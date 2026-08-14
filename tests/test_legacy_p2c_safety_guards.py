import unittest

from scrapers.counties_ga.columbia import ColumbiaScraper
from scrapers.counties_ga.coweta import CowetaScraper
from scrapers.counties_ga.dougherty import DoughertyScraper
from scrapers.counties_ga.forsyth import ForsythScraper as GAForsythScraper
from scrapers.counties_ga.hall import HallScraper
from scrapers.counties_ga.spalding import SpaldingScraper
from scrapers.counties_nc.alamance import AlamanceScraper
from scrapers.counties_nc.cabarrus import CabarrusScraper
from scrapers.counties_nc.cleveland import ClevelandScraper
from scrapers.counties_nc.forsyth import ForsythScraper as NCForsythScraper
from scrapers.counties_nc.iredell import IredellScraper
from scrapers.counties_nc.new_hanover import NewHanoverScraper
from scrapers.counties_nc.union import UnionScraper
from scrapers.counties_sc.lee import LeeScraper
from scrapers.counties_sc.lexington import LexingtonScraper


GUARDED_SCRAPERS = (
    ColumbiaScraper,
    CowetaScraper,
    DoughertyScraper,
    GAForsythScraper,
    HallScraper,
    SpaldingScraper,
    AlamanceScraper,
    CabarrusScraper,
    ClevelandScraper,
    NCForsythScraper,
    IredellScraper,
    NewHanoverScraper,
    UnionScraper,
    LeeScraper,
    LexingtonScraper,
)


class TestLegacyP2CSafetyGuards(unittest.TestCase):
    def test_all_audited_legacy_paths_are_explicitly_fail_closed(self):
        self.assertEqual(len(GUARDED_SCRAPERS), 15)
        for scraper_class in GUARDED_SCRAPERS:
            with self.subTest(scraper=scraper_class.__name__):
                scraper = scraper_class()
                self.assertFalse(scraper.SOURCE_CONTRACT_VALIDATED)
                self.assertTrue(scraper.SOURCE_SAFETY_REASON)
                self.assertEqual(scraper.scrape(), [])


if __name__ == "__main__":
    unittest.main()
