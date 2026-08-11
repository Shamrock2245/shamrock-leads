"""
Bell County (TX) Arrest Scraper — P2C / Jail Inmates platform.
URL: https://p2c.bellcountytx.com/p2c/jailinmates.aspx
"""
from scrapers.p2c_base import P2CBaseScraper


class BellScraper(P2CBaseScraper):
    P2C_URL = "https://p2c.bellcountytx.com/p2c/jailinmates.aspx"
    COUNTY_NAME = "Bell"
    FACILITY_NAME = "Bell County Jail"

    @property
    def state(self) -> str:
        return "TX"
