"""
Pulaski County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class PulaskiScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Pulaski"
        
    @property
    def portal_url(self) -> str:
        return "https://interopweb.com/pulaskisojailpop/"
