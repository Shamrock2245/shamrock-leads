"""
Murray County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class MurrayScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Murray"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://murraycountyjailga.org/"
