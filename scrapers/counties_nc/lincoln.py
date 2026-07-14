"""
Lincoln County (NC) Arrest Scraper — P2C / CentralSquare classic.
"""
from scrapers.p2c_base import P2CBaseScraper


class LincolnScraper(P2CBaseScraper):
    P2C_URL = "http://p2c.lincolnsheriff.org/jailinmates.aspx"
    COUNTY_NAME = "Lincoln"
    FACILITY_NAME = "Lincoln County Detention"

    @property
    def state(self) -> str:
        return "NC"
