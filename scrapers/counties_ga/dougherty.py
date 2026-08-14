"""
Dougherty County (GA) Arrest Scraper.
Uses P2CBaseScraper.
"""
from scrapers.p2c_base import P2CBaseScraper

class DoughertyScraper(P2CBaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_SAFETY_REASON = 'official P2C roster is access-restricted'

    @property
    def county(self) -> str:
        return "Dougherty"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://dcso.policetocitizen.com/Inmates/Catalog"
