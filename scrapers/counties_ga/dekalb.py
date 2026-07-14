"""
DeKalb County (GA) Arrest Scraper.
Uses OdysseyBaseScraper.
"""
from scrapers.odyssey_base import OdysseyBaseScraper

class DeKalbScraper(OdysseyBaseScraper):
    @property
    def county(self) -> str:
        return "DeKalb"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def base_url(self) -> str:
        return "https://correctionsrecordssearch.com/dekalbcountyga"
