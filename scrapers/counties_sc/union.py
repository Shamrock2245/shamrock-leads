"""
Union County (SC) Arrest Scraper.
Uses ZuercherBaseScraper.
"""
from scrapers.zuercher_base import ZuercherBaseScraper

class UnionScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Union"
        
    @property
    def portal_url(self) -> str:
        return "https://union-so-sc.zuercherportal.com/#/inmates"
