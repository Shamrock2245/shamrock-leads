"""
DeKalb County (AL) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: DeKalbCoAL
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class DeKalbALScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "DeKalb"

    @property
    def state(self) -> str:
        return "AL"

    @property
    def agency_id(self) -> str:
        return "DeKalbCoAL"
