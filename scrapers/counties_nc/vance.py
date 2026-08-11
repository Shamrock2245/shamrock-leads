"""
Vance County (NC) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: VanceCoNC
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class VanceScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Vance"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def agency_id(self) -> str:
        return "VanceCoNC"
