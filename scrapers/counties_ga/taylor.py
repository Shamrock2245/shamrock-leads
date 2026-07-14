"""
Taylor County (GA) Arrest Scraper.
Uses SmartCOPBaseScraper.
"""
from scrapers.smartcop_base import SmartCOPBaseScraper

class TaylorScraper(SmartCOPBaseScraper):
    @property
    def county(self) -> str:
        return "Taylor"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "http://smartcop.taylorsheriff.org:8989/SmartWEBClient/Jail.aspx"
