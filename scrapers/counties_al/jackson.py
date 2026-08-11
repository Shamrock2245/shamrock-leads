"""
Jackson County (AL) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: JacksonCoAL
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class JacksonALScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Jackson"

    @property
    def state(self) -> str:
        return "AL"

    @property
    def agency_id(self) -> str:
        return "JacksonCoAL"
