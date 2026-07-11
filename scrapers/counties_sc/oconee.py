"""
Oconee County (SC) Arrest Scraper.
Uses ZuercherBaseScraper.
"""
from scrapers.zuercher_base import ZuercherBaseScraper

class OconeeScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Oconee"
        
    @property
    def portal_url(self) -> str:
        return "https://oconee-so-sc.zuercherportal.com/#/inmates"
