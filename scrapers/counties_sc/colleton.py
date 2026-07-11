"""
Colleton County (SC) Arrest Scraper.
Uses ZuercherBaseScraper.
"""
from scrapers.zuercher_base import ZuercherBaseScraper

class ColletonScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Colleton"
        
    @property
    def portal_url(self) -> str:
        return "https://colleton-so-sc.zuercherportal.com/#/inmates"
