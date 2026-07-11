"""
Echols County (GA) Arrest Scraper.
Uses OdysseyBaseScraper.
"""
from scrapers.odyssey_base import OdysseyBaseScraper

class EcholsScraper(OdysseyBaseScraper):
    @property
    def county(self) -> str:
        return "Echols"
        
    @property
    def base_url(self) -> str:
        return "https://portalprod.lowndescounty.com/PublicAccess/"
