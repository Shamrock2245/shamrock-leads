"""
Chowan County (NC) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: ChowanCoNC
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class ChowanScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Chowan"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def agency_id(self) -> str:
        return "ChowanCoNC"
