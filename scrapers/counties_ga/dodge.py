"""
Dodge County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class DodgeScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Dodge"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://dodgecountysheriff.org/jail-population/"
