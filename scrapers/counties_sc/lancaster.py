"""
Lancaster County (SC) Arrest Scraper.
Uses NewWorldBaseScraper.
"""
from scrapers.new_world_base import NewWorldBaseScraper

class LancasterScraper(NewWorldBaseScraper):
    @property
    def county(self) -> str:
        return "Lancaster"
        
    @property
    def base_url(self) -> str:
        return "https://inmate.lancastercountysc.net/NewWorld.InmateInquiry/SC0290000"
