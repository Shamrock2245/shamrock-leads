"""
Catoosa County (GA) Arrest Scraper.
Uses Zuercher base class.
"""
from scrapers.zuercher_base import ZuercherBaseScraper

class CatoosaScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Catoosa"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def zuercher_domain(self) -> str:
        return "catoosa-so-ga.zuercherportal.com"
