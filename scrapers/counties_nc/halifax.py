"""
Halifax County (NC) Arrest Scraper — DCN DevExpress roster.
URL: https://inmates.halifaxncsheriff.com/dcn/inmates
"""
from scrapers.dcn_base import DCNBaseScraper


class HalifaxScraper(DCNBaseScraper):
    @property
    def county(self) -> str:
        return "Halifax"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def inmates_url(self) -> str:
        return "https://inmates.halifaxncsheriff.com/dcn/inmates"
