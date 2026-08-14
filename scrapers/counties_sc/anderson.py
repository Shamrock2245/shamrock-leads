"""
Anderson County (SC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class AndersonScraper(ZuercherBaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_SAFETY_REASON = 'official Zuercher portal is search-only and has no validated broad roster contract'

    @property
    def county(self) -> str:
        return "Anderson"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def zuercher_domain(self) -> str:
        return "anderson-so-sc.zuercherportal.com"
