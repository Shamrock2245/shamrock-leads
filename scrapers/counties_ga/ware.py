"""
Ware County (GA) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: WareCoGA
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class WareScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Ware"

    @property
    def state(self) -> str:
        return "GA"

    @property
    def agency_id(self) -> str:
        return "WareCoGA"
