"""
Pickens County (SC) Arrest Scraper.
Uses ZuercherBaseScraper.
"""
from scrapers.zuercher_base import ZuercherBaseScraper

class PickensScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Pickens"
        
    @property
    def portal_url(self) -> str:
        return "https://pickens-so-sc.zuercherportal.com/"
