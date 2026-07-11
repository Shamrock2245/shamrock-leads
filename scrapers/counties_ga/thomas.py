"""
Thomas County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class ThomasScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Thomas"
        
    @property
    def portal_url(self) -> str:
        return "https://www.thomascountysheriff.com/inmate-roster"
