"""
Troup County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class TroupScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Troup"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://troupcountyjailga.org/"
