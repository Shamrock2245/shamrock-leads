"""
Haralson County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class HaralsonScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Haralson"
        
    @property
    def portal_url(self) -> str:
        return "https://www.interopweb.com/haralsonjailpop/"
