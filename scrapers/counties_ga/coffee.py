"""
Coffee County (GA) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: CoffeeCoGA
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class CoffeeScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Coffee"

    @property
    def state(self) -> str:
        return "GA"

    @property
    def agency_id(self) -> str:
        return "CoffeeCoGA"
