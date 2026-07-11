"""
Oglethorpe County (GA) Arrest Scraper.
Uses Southern Software base class.
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper

class OglethorpeScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Oglethorpe"
        
    @property
    def agency_id(self) -> str:
        return "OglethorpeCoGA"
