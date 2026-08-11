"""
Warren County (NC) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: WarrenCoNC
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class WarrenScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Warren"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def agency_id(self) -> str:
        return "WarrenCoNC"
