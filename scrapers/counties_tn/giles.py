"""
Giles County (TN) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: GilesCoTN
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class GilesScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Giles"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def agency_id(self) -> str:
        return "GilesCoTN"
