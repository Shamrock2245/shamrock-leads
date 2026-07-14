"""
Lee County (SC) Arrest Scraper — P2C / CentralSquare.
"""
from scrapers.p2c_base import P2CBaseScraper


class LeeScraper(P2CBaseScraper):
    P2C_URL = "https://portal-sc-sumter-pd.centralsquarecloudgov.com/home"
    COUNTY_NAME = "Lee"
    FACILITY_NAME = "Lee County Detention"

    @property
    def state(self) -> str:
        return "SC"
