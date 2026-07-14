"""
Oconee County (SC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class OconeeScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Oconee"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def zuercher_domain(self) -> str:
        return "oconee-so-sc.zuercherportal.com"
