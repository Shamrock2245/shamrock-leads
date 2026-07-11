"""
Putnam County (GA) Arrest Scraper.
Uses SmartCOPBaseScraper.
"""
from scrapers.smartcop_base import SmartCOPBaseScraper

class PutnamScraper(SmartCOPBaseScraper):
    @property
    def county(self) -> str:
        return "Putnam"
        
    @property
    def portal_url(self) -> str:
        return "https://smartweb.pcso.us/smartwebclient/jail.aspx"
