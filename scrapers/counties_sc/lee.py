"""
Lee County (SC) Arrest Scraper.
Uses P2CBaseScraper.
"""
from scrapers.p2c_base import P2CBaseScraper

class LeeScraper(P2CBaseScraper):
    @property
    def county(self) -> str:
        return "Lee"
        
    @property
    def portal_url(self) -> str:
        return "https://portal-sc-sumter-pd.centralsquarecloudgov.com/home"
