"""
Iredell County (NC) Arrest Scraper — P2C / CentralSquare classic.
"""
from scrapers.p2c_base import P2CBaseScraper


class IredellScraper(P2CBaseScraper):
    P2C_URL = "https://p2c.iredellcountync.gov/jailinmates.aspx"
    COUNTY_NAME = "Iredell"
    FACILITY_NAME = "Iredell County Detention"

    @property
    def state(self) -> str:
        return "NC"
