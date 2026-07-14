"""
Floyd County (GA) Arrest Scraper.
Uses Zuercher base class.
"""
from scrapers.zuercher_base import ZuercherBaseScraper

class FloydScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Floyd"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def zuercher_domain(self) -> str:
        return "floyd-so-ga.zuercherportal.com"
