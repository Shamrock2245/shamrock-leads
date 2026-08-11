"""
Morgan County (AL) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: MorganCoAL
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class MorganScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Morgan"

    @property
    def state(self) -> str:
        return "AL"

    @property
    def agency_id(self) -> str:
        return "MorganCoAL"
