"""
Toombs County (GA) Arrest Scraper.
Uses ZuercherBaseScraper.
"""
from scrapers.zuercher_base import ZuercherBaseScraper

class ToombsScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Toombs"
        
    @property
    def portal_url(self) -> str:
        return "https://toombs-so-ga.zuercherportal.com/#/inmates"
