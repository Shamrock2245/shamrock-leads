"""
Kershaw County (SC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class KershawScraper(ZuercherBaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_SAFETY_REASON = 'official Zuercher roster lacks a source-issued booking or inmate ID and booking timestamp'

    @property
    def county(self) -> str:
        return "Kershaw"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def zuercher_domain(self) -> str:
        return "kershaw-so-sc.zuercherportal.com"
