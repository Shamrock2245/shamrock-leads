"""
Bacon County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class BaconScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Bacon"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://www.interopweb.com/bacon/"
