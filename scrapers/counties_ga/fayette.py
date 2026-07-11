"""
Fayette County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class FayetteScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Fayette"
        
    @property
    def portal_url(self) -> str:
        return "https://lookup.fayettesheriff.org/inmatelookup.php"
