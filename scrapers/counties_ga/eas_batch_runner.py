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
    def eas_slug(self) -> str:
        return self._slug

def run_eas_batch() -> List[ArrestRecord]:
    """Run all EAS scrapers sequentially with a polite delay."""
    all_records = []
    
    logger.info(f"🚀 Starting EAS batch runner for {len(EAS_COUNTIES)} counties...")
    start_time = time.time()
    
    for i, (county_name, slug) in enumerate(EAS_COUNTIES):
        logger.info(f"[{i+1}/{len(EAS_COUNTIES)}] Scraping {county_name} ({slug})...")
        
        scraper = DynamicEASScraper(county_name, slug)
        records = scraper.scrape()
        
        if records:
            all_records.extend(records)
            
        # Polite delay between domains to avoid rate limits
        if i < len(EAS_COUNTIES) - 1:
            time.sleep(2.0)
            
    elapsed = time.time() - start_time
    logger.info(f"🏁 EAS batch complete: {len(all_records)} total records across {len(EAS_COUNTIES)} counties in {elapsed:.1f}s")
    
    return all_records

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    run_eas_batch()
