"""
Cherokee County (SC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class CherokeeScraper(ZuercherBaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_SAFETY_REASON = 'official Zuercher portal lacks a safely validated broad roster contract'

    @property
    def county(self) -> str:
        return "Cherokee"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def zuercher_domain(self) -> str:
        return "cherokee-so-sc.zuercherportal.com"
