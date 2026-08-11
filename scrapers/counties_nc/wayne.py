"""
Wayne County (NC) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: WayneCoNC
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class WayneScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Wayne"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def agency_id(self) -> str:
        return "WayneCoNC"
