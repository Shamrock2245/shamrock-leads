"""
Sevier County (TN) Arrest Scraper — Zuercher Portal JSON API.
URL: https://seviersheriff.zuercherportal.com
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class SevierScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Sevier"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def portal_url(self) -> str:
        return "https://seviersheriff.zuercherportal.com"

    @property
    def default_facility(self) -> str:
        return "Sevier County Jail"
