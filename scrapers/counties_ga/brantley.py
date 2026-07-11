"""
Brantley County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class BrantleyScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Brantley"
        
    @property
    def portal_url(self) -> str:
        return "https://www.interopweb.com/brantley/"
