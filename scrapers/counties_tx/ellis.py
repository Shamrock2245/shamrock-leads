"""
Ellis County (TX) Arrest Scraper — P2C Inmates platform.
URL: https://p2c.co.ellis.tx.us/p2c/jailinmates.aspx
"""
from scrapers.p2c_base import P2CBaseScraper


class EllisScraper(P2CBaseScraper):
    P2C_URL = "https://p2c.co.ellis.tx.us/p2c/jailinmates.aspx"
    COUNTY_NAME = "Ellis"
    FACILITY_NAME = "Wayne McCollum Detention Center"

    @property
    def state(self) -> str:
        return "TX"
