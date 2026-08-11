"""
Effingham County (GA) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: EffinghamCoGA
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class EffinghamScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Effingham"

    @property
    def state(self) -> str:
        return "GA"

    @property
    def agency_id(self) -> str:
        return "EffinghamCoGA"
