"""
Douglas County (GA) Arrest Scraper.
Uses Zuercher base class.
"""

from scrapers.zuercher_base import ZuercherBaseScraper

class DouglasScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Douglas"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def zuercher_domain(self) -> str:
        return "douglas-so-ga.zuercherportal.com"
