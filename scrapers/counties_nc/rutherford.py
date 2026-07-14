"""
Rutherford County (NC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class RutherfordScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Rutherford"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def zuercher_domain(self) -> str:
        return "rutherford-so-nc.zuercherportal.com"
