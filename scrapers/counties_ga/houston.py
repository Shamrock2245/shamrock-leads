"""
Houston County (GA) Arrest Scraper.
Uses Zuercher base class.
"""
from scrapers.zuercher_base import ZuercherBaseScraper

class HoustonScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Houston"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def zuercher_domain(self) -> str:
        return "houston-so-ga.zuercherportal.com"
