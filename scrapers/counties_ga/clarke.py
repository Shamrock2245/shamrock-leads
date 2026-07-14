"""
Clarke County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class ClarkeScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Clarke"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "http://enigma.athensclarkecounty.com/photo/jailcurrent.asp"
