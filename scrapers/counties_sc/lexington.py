"""
Lexington County (SC) Arrest Scraper — P2C / CentralSquare.
"""
from scrapers.p2c_base import P2CBaseScraper


class LexingtonScraper(P2CBaseScraper):
    P2C_URL = "https://jail.lexingtonsheriff.net/jailinmates.aspx"
    COUNTY_NAME = "Lexington"
    FACILITY_NAME = "Lexington County Detention Center"

    @property
    def state(self) -> str:
        return "SC"
