"""
Alamance County (NC) Arrest Scraper — P2C / CentralSquare classic.
"""
from scrapers.p2c_base import P2CBaseScraper


class AlamanceScraper(P2CBaseScraper):
    P2C_URL = "https://apps.alamance-nc.com/p2c/jailinmates.aspx"
    COUNTY_NAME = "Alamance"
    FACILITY_NAME = "Alamance County Detention"

    @property
    def state(self) -> str:
        return "NC"
