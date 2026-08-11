"""
Hamblen County (TN) Arrest Scraper — Zuercher Portal JSON API.
URL: https://hamblensheriff.zuercherportal.com
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class HamblenScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Hamblen"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def portal_url(self) -> str:
        return "https://hamblensheriff.zuercherportal.com"

    @property
    def default_facility(self) -> str:
        return "Hamblen County Jail"
