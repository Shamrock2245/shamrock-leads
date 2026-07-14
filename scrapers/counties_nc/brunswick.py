"""
Brunswick County (NC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class BrunswickScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Brunswick"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def zuercher_domain(self) -> str:
        return "brunswick-so-nc.zuercherportal.com"
