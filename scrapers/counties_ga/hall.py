"""
Hall County (GA) Arrest Scraper.
Uses existing P2C base class.
"""

from scrapers.p2c_base import P2CBaseScraper

class HallScraper(P2CBaseScraper):
    @property
    def county(self) -> str:
        return "Hall"
        
    @property
    def p2c_url(self) -> str:
        return "https://hallcounty.policetocitizen.com/Inmates/Catalog"
