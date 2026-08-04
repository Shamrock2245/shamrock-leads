"""
Richmond County (NC) Arrest Scraper — DCN DevExpress roster.
URL: https://webapp01.richmondnc.com/dcn/inmates
"""
from scrapers.dcn_base import DCNBaseScraper


class RichmondScraper(DCNBaseScraper):
    @property
    def county(self) -> str:
        return "Richmond"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def inmates_url(self) -> str:
        return "https://webapp01.richmondnc.com/dcn/inmates"
