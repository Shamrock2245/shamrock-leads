"""
Moore County (NC) Arrest Scraper — DCN DevExpress roster.
URL: https://webapps.moorecountync.gov/dcn/inmates
"""
from scrapers.dcn_base import DCNBaseScraper


class MooreScraper(DCNBaseScraper):
    @property
    def county(self) -> str:
        return "Moore"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def inmates_url(self) -> str:
        return "https://webapps.moorecountync.gov/dcn/inmates"
