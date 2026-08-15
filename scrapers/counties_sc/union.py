"""
Union County (SC) Arrest Scraper — Zuercher portal.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class UnionScraper(ZuercherBaseScraper):

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "No configured public roster URL is documented for this inherited source path; source retrieval is not permitted."
    )
    @property
    def county(self) -> str:
        return "Union"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def zuercher_domain(self) -> str:
        return "union-so-sc.zuercherportal.com"
