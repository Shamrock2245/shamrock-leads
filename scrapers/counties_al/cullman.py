"""
Cullman County (AL) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: CullmanCoAL
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class CullmanScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Cullman"

    @property
    def state(self) -> str:
        return "AL"

    @property
    def agency_id(self) -> str:
        return "CullmanCoAL"
