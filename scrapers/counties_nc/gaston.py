"""
Gaston County (NC) Arrest Scraper — New World InmateInquiry.
"""
from scrapers.new_world_base import NewWorldBaseScraper


class GastonScraper(NewWorldBaseScraper):
    @property
    def county(self) -> str:
        return "Gaston"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def base_url(self) -> str:
        return "https://tepsweb.cityofgastonia.com/NewWorld.InmateInquiry/GastonCounty"
