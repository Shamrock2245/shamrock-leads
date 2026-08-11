"""
Bradley County (TN) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: BradleyCoTN
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class BradleyScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Bradley"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def agency_id(self) -> str:
        return "BradleyCoTN"
