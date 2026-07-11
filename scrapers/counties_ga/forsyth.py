"""
Forsyth County (GA) Arrest Scraper.
Uses existing P2C base class.
"""

from scrapers.p2c_base import P2CBaseScraper

class ForsythScraper(P2CBaseScraper):
    @property
    def county(self) -> str:
        return "Forsyth"
        
    @property
    def p2c_url(self) -> str:
        return "https://forsythsheriffga.policetocitizen.com/Inmates/Catalog"
