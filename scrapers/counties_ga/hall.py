"""
Hall County (GA) Arrest Scraper.
Uses existing P2C base class.
"""

from scrapers.p2c_base import P2CBaseScraper

class HallScraper(P2CBaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_SAFETY_REASON = 'official P2C roster is access-restricted and lacks a verified source ID boundary'

    @property
    def county(self) -> str:
        return "Hall"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def p2c_url(self) -> str:
        return "https://hallcounty.policetocitizen.com/Inmates/Catalog"
