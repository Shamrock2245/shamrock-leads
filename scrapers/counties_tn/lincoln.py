"""
Lincoln County (TN) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: LincolnCoTN
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class LincolnTNScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Lincoln"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def agency_id(self) -> str:
        return "LincolnCoTN"
