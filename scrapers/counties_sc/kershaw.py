"""
Kershaw County (SC) Arrest Scraper.
Uses ZuercherBaseScraper.
"""
from scrapers.zuercher_base import ZuercherBaseScraper

class KershawScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Kershaw"
        
    @property
    def portal_url(self) -> str:
        return "https://kershaw-so-sc.zuercherportal.com/#/inmates"
