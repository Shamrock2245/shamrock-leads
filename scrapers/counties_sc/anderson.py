"""
Anderson County (SC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class AndersonScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Anderson"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def zuercher_domain(self) -> str:
        return "anderson-so-sc.zuercherportal.com"
