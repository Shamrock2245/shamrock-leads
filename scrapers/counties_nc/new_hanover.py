"""
New Hanover County (NC) Arrest Scraper — P2C / CentralSquare classic.
"""
from scrapers.p2c_base import P2CBaseScraper


class NewHanoverScraper(P2CBaseScraper):
    P2C_URL = "https://p2c.nhcgov.com/p2c/jailinmates.aspx"
    COUNTY_NAME = "New Hanover"
    FACILITY_NAME = "New Hanover County Detention"

    @property
    def state(self) -> str:
        return "NC"
