"""
Dorchester County (SC) Arrest Scraper — Southern Software Citizen Connect.
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class DorchesterScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Dorchester"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def agency_id(self) -> str:
        return "DorchesterCoSC"
