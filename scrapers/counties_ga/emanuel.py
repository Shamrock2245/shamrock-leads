"""
Emanuel County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class EmanuelScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Emanuel"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://interopweb.com/emanueljailpop/"
