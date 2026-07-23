"""
Forsyth County (NC) Arrest Scraper — P2C / CentralSquare classic.

Portal: https://p2c.fcso.us/p2c/jailinmates.aspx
Forsyth County (Winston-Salem) uses the standard P2C platform.
Extends P2CBaseScraper for zero-custom-code implementation.
"""
from scrapers.p2c_base import P2CBaseScraper


class ForsythScraper(P2CBaseScraper):
    P2C_URL = "https://p2c.fcso.us/p2c/jailinmates.aspx"
    COUNTY_NAME = "Forsyth"
    FACILITY_NAME = "Forsyth County Detention Center"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def scraper_id(self) -> str:
        return "scraper_nc_forsyth"
