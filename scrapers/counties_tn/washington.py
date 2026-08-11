"""
Washington County (TN) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: WashingtonCoTN
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class WashingtonScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Washington"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def agency_id(self) -> str:
        return "WashingtonCoTN"
