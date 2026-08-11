"""
Robertson County (TN) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: RobertsonCoTN
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class RobertsonScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Robertson"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def agency_id(self) -> str:
        return "RobertsonCoTN"
