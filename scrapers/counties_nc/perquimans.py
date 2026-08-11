"""
Perquimans County (NC) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: PerquimansCoNC
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class PerquimansScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Perquimans"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def agency_id(self) -> str:
        return "PerquimansCoNC"
