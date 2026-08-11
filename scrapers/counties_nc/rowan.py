"""
Rowan County (NC) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: RowanCoNC
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class RowanScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Rowan"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def agency_id(self) -> str:
        return "RowanCoNC"
