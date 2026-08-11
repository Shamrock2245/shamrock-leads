"""
Johnson County (TX) Arrest Scraper — P2C Inmates platform.
URL: https://p2c.johnsoncountytx.org/p2c/jailinmates.aspx
"""
from scrapers.p2c_base import P2CBaseScraper


class JohnsonScraper(P2CBaseScraper):
    P2C_URL = "https://p2c.johnsoncountytx.org/p2c/jailinmates.aspx"
    COUNTY_NAME = "Johnson"
    FACILITY_NAME = "Johnson County Corrections Center"

    @property
    def state(self) -> str:
        return "TX"
