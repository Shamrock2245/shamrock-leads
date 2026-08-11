"""
Laurens County (GA) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: LaurensCoGA
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class LaurensScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Laurens"

    @property
    def state(self) -> str:
        return "GA"

    @property
    def agency_id(self) -> str:
        return "LaurensCoGA"
