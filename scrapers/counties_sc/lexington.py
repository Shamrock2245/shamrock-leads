"""
Lexington County (SC) Arrest Scraper — P2C / CentralSquare.
"""
from scrapers.p2c_base import P2CBaseScraper


class LexingtonScraper(P2CBaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_SAFETY_REASON = 'official P2C portal is search-only'

    P2C_URL = "https://jail.lexingtonsheriff.net/jailinmates.aspx"
    COUNTY_NAME = "Lexington"
    FACILITY_NAME = "Lexington County Detention Center"

    @property
    def state(self) -> str:
        return "SC"
