"""
Dorchester County (SC) Arrest Scraper.
Uses SouthernSoftwareBaseScraper.
"""
from scrapers.southern_sw_base import SouthernSoftwareBaseScraper

class DorchesterScraper(SouthernSoftwareBaseScraper):
    @property
    def county(self) -> str:
        return "Dorchester"
        
    @property
    def agency_id(self) -> str:
        return "DorchesterCoSC"
