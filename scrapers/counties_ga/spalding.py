"""
Spalding County (GA) Arrest Scraper.
Uses P2CBaseScraper.
"""
from scrapers.p2c_base import P2CBaseScraper

class SpaldingScraper(P2CBaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_SAFETY_REASON = 'official P2C roster lacks a source-issued booking or inmate ID and booking timestamp'

    @property
    def county(self) -> str:
        return "Spalding"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "http://208.97.5.12/jailinmates.aspx"
