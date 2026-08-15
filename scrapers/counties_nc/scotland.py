"""
Scotland County (NC) Arrest Scraper — Southern Software Citizen Connect.
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class ScotlandScraper(SouthernSWBaseScraper):

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "No configured public roster URL is documented for this inherited source path; source retrieval is not permitted."
    )
    @property
    def county(self) -> str:
        return "Scotland"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def agency_id(self) -> str:
        return "ScotlandCoNC"
