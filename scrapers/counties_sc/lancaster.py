"""
Lancaster County (SC) Arrest Scraper — New World InmateInquiry.
"""
from scrapers.new_world_base import NewWorldBaseScraper


class LancasterScraper(NewWorldBaseScraper):
    @property
    def county(self) -> str:
        return "Lancaster"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def base_url(self) -> str:
        return "https://inmate.lancastercountysc.net/NewWorld.InmateInquiry/SC0290000"
