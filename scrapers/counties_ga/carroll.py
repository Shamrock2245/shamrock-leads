"""
Carroll County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class CarrollScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Carroll"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "http://www.interopweb.com/Carroll"
