"""
Sumter County (GA) Arrest Scraper.
Uses SmartCOPBaseScraper.
"""
from scrapers.smartcop_base import SmartCOPBaseScraper

class SumterScraper(SmartCOPBaseScraper):
    @property
    def county(self) -> str:
        return "Sumter"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://portal.sumtercountysheriff.org/smartwebclient/jail.aspx"
