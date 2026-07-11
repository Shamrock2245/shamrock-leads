"""
Henry County (GA) Arrest Scraper.
Uses NewWorldBaseScraper.
"""
from scrapers.new_world_base import NewWorldBaseScraper

class HenryScraper(NewWorldBaseScraper):
    @property
    def county(self) -> str:
        return "Henry"
        
    @property
    def base_url(self) -> str:
        return "https://inmatesearch.co.henry.ga.us/"
