"""
Kershaw County (SC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class KershawScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Kershaw"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def zuercher_domain(self) -> str:
        return "kershaw-so-sc.zuercherportal.com"
