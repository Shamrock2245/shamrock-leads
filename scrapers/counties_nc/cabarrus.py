"""
Cabarrus County (NC) Arrest Scraper — P2C / CentralSquare classic.
"""
from scrapers.p2c_base import P2CBaseScraper


class CabarrusScraper(P2CBaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_SAFETY_REASON = 'official P2C portal is search-only'

    P2C_URL = "https://onlineservices.cabarruscounty.us/p2c/jailinmates.aspx"
    COUNTY_NAME = "Cabarrus"
    FACILITY_NAME = "Cabarrus County Detention"

    @property
    def state(self) -> str:
        return "NC"
