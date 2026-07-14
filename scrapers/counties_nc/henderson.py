"""
Henderson County (NC) Arrest Scraper — Southern Software Citizen Connect.
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class HendersonScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Henderson"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def agency_id(self) -> str:
        return "HendersonCoNC"
