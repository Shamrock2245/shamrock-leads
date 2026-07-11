"""
Cherokee County (SC) Arrest Scraper.
Uses ZuercherBaseScraper.
"""
from scrapers.zuercher_base import ZuercherBaseScraper

class CherokeeScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Cherokee"
        
    @property
    def portal_url(self) -> str:
        return "https://cherokee-so-sc.zuercherportal.com/"
