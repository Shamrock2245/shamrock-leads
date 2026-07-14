"""
Hoke County (NC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class HokeScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Hoke"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def zuercher_domain(self) -> str:
        return "hoke-so-nc.zuercherportal.com"
