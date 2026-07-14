"""
McIntosh County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class McIntoshScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "McIntosh"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://mcintoshjailroster.org/"
