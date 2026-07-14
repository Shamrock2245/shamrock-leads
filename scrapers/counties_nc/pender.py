"""
Pender County (NC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class PenderScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Pender"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def zuercher_domain(self) -> str:
        return "pender-so-nc.zuercherportal.com"
