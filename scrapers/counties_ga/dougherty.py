"""
Dougherty County (GA) Arrest Scraper.
Uses P2CBaseScraper.
"""
from scrapers.p2c_base import P2CBaseScraper

class DoughertyScraper(P2CBaseScraper):
    @property
    def county(self) -> str:
        return "Dougherty"
        
    @property
    def portal_url(self) -> str:
        return "https://dcso.policetocitizen.com/Inmates/Catalog"
