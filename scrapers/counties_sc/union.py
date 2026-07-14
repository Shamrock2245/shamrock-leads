"""
Union County (SC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class UnionScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Union"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def zuercher_domain(self) -> str:
        return "union-so-sc.zuercherportal.com"
