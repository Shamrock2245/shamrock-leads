"""
Wilkes County (NC) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: WilkesCoNC
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class WilkesScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Wilkes"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def agency_id(self) -> str:
        return "WilkesCoNC"
