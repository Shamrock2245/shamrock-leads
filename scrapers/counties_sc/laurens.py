"""
Laurens County (SC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class LaurensScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Laurens"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def zuercher_domain(self) -> str:
        return "laurens-911-sc.zuercherportal.com"
