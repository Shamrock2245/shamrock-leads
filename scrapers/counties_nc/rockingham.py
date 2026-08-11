"""
Rockingham County (NC) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: RockinghamCoNC
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class RockinghamScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Rockingham"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def agency_id(self) -> str:
        return "RockinghamCoNC"
