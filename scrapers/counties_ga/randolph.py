"""
Randolph County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class RandolphScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Randolph"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://www.randolphcountyso.org/inmate_roster.php"
