"""
Baker County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class BakerScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Baker"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://www.interopweb.com/baker/"
