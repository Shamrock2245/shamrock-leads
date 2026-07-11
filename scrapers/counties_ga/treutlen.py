"""
Treutlen County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class TreutlenScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Treutlen"
        
    @property
    def portal_url(self) -> str:
        return "https://treutlenjailroster.org/"
