"""
Lee County (GA) Arrest Scraper.
Uses Southern Software base class.
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper

class LeeScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Lee"
        
    @property
    def agency_id(self) -> str:
        return "LeeCoSOGA"
