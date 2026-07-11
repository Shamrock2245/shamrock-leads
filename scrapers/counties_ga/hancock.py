"""
Hancock County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class HancockScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Hancock"
        
    @property
    def portal_url(self) -> str:
        return "https://www.hancockso.com/InmateRoster/hancock_inmatelist.html"
