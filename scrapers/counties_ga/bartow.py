"""
Bartow County (GA) Arrest Scraper.
Uses NewWorldBaseScraper.
"""
from scrapers.new_world_base import NewWorldBaseScraper

class BartowScraper(NewWorldBaseScraper):
    @property
    def county(self) -> str:
        return "Bartow"
        
    @property
    def base_url(self) -> str:
        return "https://jailroster.bc-cville.org/NewWorld.InmateInquiry/bartow"
