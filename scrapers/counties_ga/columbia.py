"""
Columbia County (GA) Arrest Scraper.
Uses P2CBaseScraper.
"""
from scrapers.p2c_base import P2CBaseScraper

class ColumbiaScraper(P2CBaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_SAFETY_REASON = 'official custody list is unavailable and legacy P2C access is restricted'

    @property
    def county(self) -> str:
        return "Columbia"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://columbiacountyso.policetocitizen.com/Inmates/Catalog"
