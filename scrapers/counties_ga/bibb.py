"""
Bibb County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class BibbScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Bibb"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://www.interopweb.com/bibb/"
