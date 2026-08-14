"""
Union County (NC) Arrest Scraper — P2C / CentralSquare classic.
"""
from scrapers.p2c_base import P2CBaseScraper


class UnionScraper(P2CBaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_SAFETY_REASON = 'official P2C roster is CAPTCHA-protected and lacks a source ID boundary'

    P2C_URL = "https://sheriff.unioncountync.gov/jailinmates.aspx"
    COUNTY_NAME = "Union"
    FACILITY_NAME = "Union County Detention"

    @property
    def state(self) -> str:
        return "NC"
