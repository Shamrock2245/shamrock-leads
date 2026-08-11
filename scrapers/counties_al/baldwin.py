"""
Baldwin County (AL) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: BaldwinCoAL
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class BaldwinScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Baldwin"

    @property
    def state(self) -> str:
        return "AL"

    @property
    def agency_id(self) -> str:
        return "BaldwinCoAL"
