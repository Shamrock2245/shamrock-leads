"""
Columbia County (GA) Arrest Scraper.
Uses P2CBaseScraper.
"""
from scrapers.p2c_base import P2CBaseScraper

class ColumbiaScraper(P2CBaseScraper):
    @property
    def county(self) -> str:
        return "Columbia"
        
    @property
    def portal_url(self) -> str:
        return "https://columbiacountyso.policetocitizen.com/Inmates/Catalog"
