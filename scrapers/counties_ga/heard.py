"""
Heard County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class HeardScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Heard"
        
    @property
    def portal_url(self) -> str:
        return "https://www.franklinsheriff.org/roster.php"
