"""
Bleckley County (GA) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: BleckleyCoGA
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class BleckleyScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Bleckley"

    @property
    def state(self) -> str:
        return "GA"

    @property
    def agency_id(self) -> str:
        return "BleckleyCoGA"
