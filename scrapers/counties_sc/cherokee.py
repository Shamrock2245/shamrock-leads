"""
Cherokee County (SC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class CherokeeScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Cherokee"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def zuercher_domain(self) -> str:
        return "cherokee-so-sc.zuercherportal.com"
