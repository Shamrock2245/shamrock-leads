"""
Nash County (NC) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: NashCoNC
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class NashScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Nash"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def agency_id(self) -> str:
        return "NashCoNC"
