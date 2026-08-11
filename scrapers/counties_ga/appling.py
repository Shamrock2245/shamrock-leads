"""
Appling County (GA) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: ApplingCoGA
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class ApplingScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Appling"

    @property
    def state(self) -> str:
        return "GA"

    @property
    def agency_id(self) -> str:
        return "ApplingCoGA"
