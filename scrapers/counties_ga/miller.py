"""
Miller County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class MillerScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Miller"
        
    @property
    def portal_url(self) -> str:
        return "https://www.millercountysheriff.org/roster.php"
