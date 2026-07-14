"""
Barrow County (GA) Arrest Scraper.
Uses NewWorldBaseScraper.
"""
from scrapers.new_world_base import NewWorldBaseScraper

class BarrowScraper(NewWorldBaseScraper):
    @property
    def county(self) -> str:
        return "Barrow"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def base_url(self) -> str:
        return "http://jail.barrowsheriff.com:8095/NewWorld.InmateInquiry/GA0070000"
