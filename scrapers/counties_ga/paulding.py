"""
Paulding County (GA) Arrest Scraper.
Uses NewWorldBaseScraper.
"""
from scrapers.new_world_base import NewWorldBaseScraper

class PauldingScraper(NewWorldBaseScraper):
    @property
    def county(self) -> str:
        return "Paulding"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def base_url(self) -> str:
        return "https://inmate.paulding.gov:9443/NewWorld.InmateInquiry/GA1100000"
