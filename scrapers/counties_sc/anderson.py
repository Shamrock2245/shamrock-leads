"""
Anderson County (SC) Arrest Scraper.
Uses ZuercherBaseScraper.
"""
from scrapers.zuercher_base import ZuercherBaseScraper

class AndersonScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Anderson"
        
    @property
    def portal_url(self) -> str:
        return "https://anderson-so-sc.zuercherportal.com/#/inmates"
