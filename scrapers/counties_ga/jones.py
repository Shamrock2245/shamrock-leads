"""
Jones County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class JonesScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Jones"
        
    @property
    def portal_url(self) -> str:
        return "https://www.jcsheriff.org/inmateSearch"
