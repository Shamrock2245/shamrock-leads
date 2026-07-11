"""
Crawford County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class CrawfordScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Crawford"
        
    @property
    def portal_url(self) -> str:
        return "https://inmates.crawfordcountysheriff.org/"
