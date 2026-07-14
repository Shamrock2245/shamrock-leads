"""
Muscogee County (GA) Arrest Scraper.
Uses OdysseyBaseScraper.
"""
from scrapers.odyssey_base import OdysseyBaseScraper

class MuscogeeScraper(OdysseyBaseScraper):
    @property
    def county(self) -> str:
        return "Muscogee"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def base_url(self) -> str:
        return "https://portal-gamuscogee.tylertech.cloud/app/JailSearch/"
