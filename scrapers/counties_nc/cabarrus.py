"""
Cabarrus County (NC) Arrest Scraper — P2C / CentralSquare classic.
"""
from scrapers.p2c_base import P2CBaseScraper


class CabarrusScraper(P2CBaseScraper):
    P2C_URL = "https://onlineservices.cabarruscounty.us/p2c/jailinmates.aspx"
    COUNTY_NAME = "Cabarrus"
    FACILITY_NAME = "Cabarrus County Detention"

    @property
    def state(self) -> str:
        return "NC"
