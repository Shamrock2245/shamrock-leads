"""
Decatur County (GA) Arrest Scraper.
Uses Southern Software base class.
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper

class DecaturScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Decatur"
        
    @property
    def agency_id(self) -> str:
        return "DecaturCoSOGA"
