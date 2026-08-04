"""
Carteret County (NC) Arrest Scraper — DCN DevExpress roster.

Portal: https://inmateinfo.carteretcountync.gov/
Roster: https://inmateinfo.carteretcountync.gov/inmates
"""
from scrapers.dcn_base import DCNBaseScraper


class CarteretScraper(DCNBaseScraper):
    @property
    def county(self) -> str:
        return "Carteret"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def inmates_url(self) -> str:
        return "https://inmateinfo.carteretcountync.gov/inmates"
