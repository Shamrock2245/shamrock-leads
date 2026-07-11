"""
Laurens County (SC) Arrest Scraper.
Uses ZuercherBaseScraper.
"""
from scrapers.zuercher_base import ZuercherBaseScraper

class LaurensScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Laurens"
        
    @property
    def portal_url(self) -> str:
        return "https://laurens-911-sc.zuercherportal.com/#/inmates"
