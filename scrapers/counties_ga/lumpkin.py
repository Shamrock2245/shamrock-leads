"""
Lumpkin County (GA) Arrest Scraper.
Uses ZuercherBaseScraper.
"""
from scrapers.zuercher_base import ZuercherBaseScraper

class LumpkinScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Lumpkin"
        
    @property
    def portal_url(self) -> str:
        return "https://lumpkin-so-ga.zuercherportal.com/"
