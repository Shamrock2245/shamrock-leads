"""
Lee County (NC) Arrest Scraper — DCN DevExpress roster.
URL: https://dcn.leecountync.gov/dcn/inmates

Note: CLI key is ``nc_lee`` (not FL Lee).
"""
from scrapers.dcn_base import DCNBaseScraper


class LeeScraper(DCNBaseScraper):
    @property
    def county(self) -> str:
        return "Lee"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def inmates_url(self) -> str:
        return "https://dcn.leecountync.gov/dcn/inmates"
