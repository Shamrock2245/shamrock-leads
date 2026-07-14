"""
Camden County (GA) Arrest Scraper.
Uses NewWorldBaseScraper.
"""
from scrapers.new_world_base import NewWorldBaseScraper

class CamdenScraper(NewWorldBaseScraper):
    @property
    def county(self) -> str:
        return "Camden"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def base_url(self) -> str:
        return "http://66.222.93.2/NewWorld.InmateInquiry/CamdenCounty/"
