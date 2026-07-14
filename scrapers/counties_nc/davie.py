"""
Davie County (NC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class DavieScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Davie"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def zuercher_domain(self) -> str:
        return "davie-so-nc.zuercherportal.com"
