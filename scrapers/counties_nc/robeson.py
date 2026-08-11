"""
Robeson County (NC) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: RobesonCoNC
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class RobesonScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Robeson"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def agency_id(self) -> str:
        return "RobesonCoNC"
