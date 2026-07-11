"""
Cherokee County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class CherokeeScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Cherokee"
        
    @property
    def portal_url(self) -> str:
        return "https://sheriff.cherokeecountyga.gov/jaillist/inmate-search-report.php"
