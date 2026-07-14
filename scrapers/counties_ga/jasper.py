"""
Jasper County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class JasperScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Jasper"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://jasperso.com/inmate-roster/"
