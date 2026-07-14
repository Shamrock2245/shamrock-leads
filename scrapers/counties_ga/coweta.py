"""
Coweta County (GA) Arrest Scraper.
Uses P2CBaseScraper.
"""
from scrapers.p2c_base import P2CBaseScraper

class CowetaScraper(P2CBaseScraper):
    @property
    def county(self) -> str:
        return "Coweta"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://cowetacountyjailga.org/"
