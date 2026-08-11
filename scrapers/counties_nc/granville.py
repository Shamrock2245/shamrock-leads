"""
Granville County (NC) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: GranvilleCoNC
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class GranvilleScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Granville"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def agency_id(self) -> str:
        return "GranvilleCoNC"
