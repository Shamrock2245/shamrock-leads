"""
Jefferson County (TX) Arrest Scraper — P2C Inmates.
URL: https://p2c.co.jefferson.tx.us/p2c/jailinmates.aspx
"""
from scrapers.p2c_base import P2CBaseScraper


class JeffersonScraper(P2CBaseScraper):
    P2C_URL = "https://p2c.co.jefferson.tx.us/p2c/jailinmates.aspx"
    COUNTY_NAME = "Jefferson"
    FACILITY_NAME = "Jefferson County Downtown Jail"

    @property
    def state(self) -> str:
        return "TX"
