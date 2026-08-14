"""
Iredell County (NC) Arrest Scraper — P2C / CentralSquare classic.
"""
from scrapers.p2c_base import P2CBaseScraper


class IredellScraper(P2CBaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_SAFETY_REASON = 'official P2C portal is search-only'

    P2C_URL = "https://p2c.iredellcountync.gov/jailinmates.aspx"
    COUNTY_NAME = "Iredell"
    FACILITY_NAME = "Iredell County Detention"

    @property
    def state(self) -> str:
        return "NC"
