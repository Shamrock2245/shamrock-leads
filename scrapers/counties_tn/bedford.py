"""
Bedford County (TN) Arrest Scraper — Southern Software Citizen Connect.
AgencyID: BedfordCoTN
"""
from scrapers.southern_sw_base import SouthernSWBaseScraper


class BedfordScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Bedford"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def agency_id(self) -> str:
        return "BedfordCoTN"
