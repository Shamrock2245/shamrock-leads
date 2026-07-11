"""
Lexington County (SC) Arrest Scraper.
Uses P2CBaseScraper.
"""
from scrapers.p2c_base import P2CBaseScraper

class LexingtonScraper(P2CBaseScraper):
    @property
    def county(self) -> str:
        return "Lexington"
        
    @property
    def portal_url(self) -> str:
        return "https://jail.lexingtonsheriff.net/jailinmates.aspx"
