"""
Crisp County (GA) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: CrispCoGA
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class CrispScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Crisp"

    @property
    def state(self) -> str:
        return "GA"

    @property
    def agency_id(self) -> str:
        return "CrispCoGA"
