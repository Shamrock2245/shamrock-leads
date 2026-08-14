"""
Forsyth County (GA) Arrest Scraper.
Uses existing P2C base class.
"""

from scrapers.p2c_base import P2CBaseScraper

class ForsythScraper(P2CBaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_SAFETY_REASON = 'official P2C roster is protected by CAPTCHA or WAF'

    @property
    def county(self) -> str:
        return "Forsyth"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def p2c_url(self) -> str:
        return "https://forsythsheriffga.policetocitizen.com/Inmates/Catalog"
