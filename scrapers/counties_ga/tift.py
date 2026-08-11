"""
Tift County (GA) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: TiftCoGA
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class TiftScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Tift"

    @property
    def state(self) -> str:
        return "GA"

    @property
    def agency_id(self) -> str:
        return "TiftCoGA"
