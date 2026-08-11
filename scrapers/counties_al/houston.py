"""
Houston County (AL) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: HoustonCoAL
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class HoustonScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Houston"

    @property
    def state(self) -> str:
        return "AL"

    @property
    def agency_id(self) -> str:
        return "HoustonCoAL"
