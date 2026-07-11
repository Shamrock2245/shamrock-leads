"""
Spalding County (GA) Arrest Scraper.
Uses P2CBaseScraper.
"""
from scrapers.p2c_base import P2CBaseScraper

class SpaldingScraper(P2CBaseScraper):
    @property
    def county(self) -> str:
        return "Spalding"
        
    @property
    def portal_url(self) -> str:
        return "http://208.97.5.12/jailinmates.aspx"
