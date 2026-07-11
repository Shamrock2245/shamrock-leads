"""
Rockdale County (GA) Arrest Scraper.
Uses OdysseyBaseScraper.
"""
from scrapers.odyssey_base import OdysseyBaseScraper

class RockdaleScraper(OdysseyBaseScraper):
    @property
    def county(self) -> str:
        return "Rockdale"
        
    @property
    def base_url(self) -> str:
        return "https://portal-garockdale.tylertech.cloud/JailSearch/default.aspx"
