"""
Colleton County (SC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class ColletonScraper(ZuercherBaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_SAFETY_REASON = 'official Zuercher roster lacks a source-issued booking or inmate ID and booking timestamp'

    @property
    def county(self) -> str:
        return "Colleton"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def zuercher_domain(self) -> str:
        return "colleton-so-sc.zuercherportal.com"
