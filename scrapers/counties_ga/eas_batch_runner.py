"""
Batch runner for all Eagle Advantage Solutions (EAS) counties in Georgia.
Since they all share the offenderindex.com platform, we can scrape them
sequentially in one process to save overhead.
"""

import logging
import time
from typing import List

from scrapers.eas_base import EASBaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# List of confirmed EAS counties and their slugs
EAS_COUNTIES = [
    ("Atkinson", "atkinsoncoga"),
    ("Ben Hill", "benhillcoga"),
    ("Berrien", "berriencoga"),
    ("Butts", "buttscoga"),
    ("Chattooga", "chattoogacoga"),
    ("Cook", "cookcoga"),
    ("Decatur", "decaturcoga"),
    ("Elbert", "elbertcoga"),
    ("Fannin", "fannincoga"),
    ("Gilmer", "gilmercoga"),
    ("Gordon", "gordoncoga"),
    ("Jackson", "jacksoncoga"),
    ("Jeff Davis", "jeffdaviscoga"),
    ("Jenkins", "jenkinscoga"),
    ("Laurens", "laurenscoga"),
    ("Lee", "leecoga"),
    ("Lincoln", "lincolncoga"),
    ("Madison", "madisoncoga"),
    ("Newton", "newtoncoga"),
    ("Pierce", "piercecoga"),
    ("Tift", "tiftcoga"),
    ("Towns", "townscoga"),
    ("Ware", "warecoga"),
    ("Wayne", "waynecoga"),
    ("Webster", "webstercoga"),
    ("Wheeler", "wheelercoga"),
    ("McDuffie", "mcduffiecoga"),
    ("Meriwether", "meriwethercoga"),
    ("Warren", "warrencoga"),
    ("Worth", "worthcoga")
]

class DynamicEASScraper(EASBaseScraper):
    """Dynamically configured EAS scraper."""
    def __init__(self, county_name: str, slug: str):
        super().__init__()
        self._county = county_name
        self._slug = slug
        
    @property
    def county(self) -> str:
        return self._county

    @property
    def state(self) -> str:
        return "GA"

    @property
    def eas_slug(self) -> str:
        return self._slug

def run_eas_batch() -> List[ArrestRecord]:
    """Fetch EAS roster pages sequentially for manual reconnaissance only.

    This helper is intentionally not registered with the scheduler. EAS sources
    must not be treated as production coverage until each live endpoint and its
    stable booking identifier are revalidated.
    """
    all_records: List[ArrestRecord] = []
    logger.info("Starting EAS reconnaissance batch for %d counties", len(EAS_COUNTIES))
    start_time = time.time()

    for i, (county_name, slug) in enumerate(EAS_COUNTIES):
        logger.info("[%d/%d] Fetching %s", i + 1, len(EAS_COUNTIES), county_name)
        scraper = DynamicEASScraper(county_name, slug)
        records = scraper.scrape()
        if records:
            all_records.extend(records)

        # Respect the shared upstream with a deliberate delay between counties.
        if i < len(EAS_COUNTIES) - 1:
            time.sleep(2.0)

    elapsed = time.time() - start_time
    logger.info(
        "EAS reconnaissance batch complete: %d records across %d counties in %.1fs",
        len(all_records),
        len(EAS_COUNTIES),
        elapsed,
    )
    return all_records

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    run_eas_batch()
