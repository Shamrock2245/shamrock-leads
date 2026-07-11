"""
Chesterfield County (SC) Arrest Scraper.
Uses SouthernSoftwareBaseScraper.
"""
from scrapers.southern_sw_base import SouthernSoftwareBaseScraper

class ChesterfieldScraper(SouthernSoftwareBaseScraper):
    @property
    def county(self) -> str:
        return "Chesterfield"
        
    @property
    def agency_id(self) -> str:
        return "ChesterfieldCoDetCtr"
