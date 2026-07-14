"""
Pickens County (SC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class PickensScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Pickens"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def zuercher_domain(self) -> str:
        return "pickens-so-sc.zuercherportal.com"
