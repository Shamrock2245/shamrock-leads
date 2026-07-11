"""
Polk County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class PolkScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Polk"
        
    @property
    def portal_url(self) -> str:
        return "https://www.interopweb.com/polkjailpop/"
