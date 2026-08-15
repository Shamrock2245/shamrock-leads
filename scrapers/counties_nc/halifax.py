"""
Halifax County (NC) Arrest Scraper — DCN DevExpress roster.
URL: https://inmates.halifaxncsheriff.com/dcn/inmates
"""
from scrapers.dcn_base import DCNBaseScraper


class HalifaxScraper(DCNBaseScraper):

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "The configured Halifax DCN path was unavailable through ordinary access; no booking-safe broad roster contract is revalidated."
    )
    @property
    def county(self) -> str:
        return "Halifax"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def inmates_url(self) -> str:
        return "https://inmates.halifaxncsheriff.com/dcn/inmates"
