"""
Person County (NC) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: PersonCoNC
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class PersonScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Person"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def agency_id(self) -> str:
        return "PersonCoNC"
