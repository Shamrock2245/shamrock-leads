"""
Cleveland County (NC) Arrest Scraper — P2C / CentralSquare classic.
"""
from scrapers.p2c_base import P2CBaseScraper


class ClevelandScraper(P2CBaseScraper):
    P2C_URL = "http://74.218.167.200/p2c/jailinmates.aspx"
    COUNTY_NAME = "Cleveland"
    FACILITY_NAME = "Cleveland County Detention"

    @property
    def state(self) -> str:
        return "NC"
