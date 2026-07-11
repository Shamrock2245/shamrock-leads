"""
Macon County (GA) Arrest Scraper.
Uses OdysseyBaseScraper.
"""
from scrapers.odyssey_base import OdysseyBaseScraper

class MaconScraper(OdysseyBaseScraper):
    @property
    def county(self) -> str:
        return "Macon"
        
    @property
    def base_url(self) -> str:
        return "http://50.77.170.147/NewWorld.InmateInquiry/IL0580000/"
