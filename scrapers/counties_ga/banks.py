"""
Banks County (GA) Arrest Scraper.
Uses Southern Software base class.
"""

from scrapers.southern_sw_base import SouthernSWBaseScraper

class BanksScraper(SouthernSWBaseScraper):
    @property
    def county(self) -> str:
        return "Banks"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def agency_id(self) -> str:
        return "BanksCoGA"
