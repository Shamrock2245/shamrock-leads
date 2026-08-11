"""
Guadalupe County (TX) Arrest Scraper — P2C Inmates platform.
URL: https://p2c.co.guadalupe.tx.us/p2c/jailinmates.aspx
"""
from scrapers.p2c_base import P2CBaseScraper


class GuadalupeScraper(P2CBaseScraper):
    P2C_URL = "https://p2c.co.guadalupe.tx.us/p2c/jailinmates.aspx"
    COUNTY_NAME = "Guadalupe"
    FACILITY_NAME = "Guadalupe County Adult Detention Center"

    @property
    def state(self) -> str:
        return "TX"
