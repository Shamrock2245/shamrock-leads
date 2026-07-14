"""
Grady County (GA) Arrest Scraper.
Uses InteropWeb base class.
"""
from scrapers.interopweb_base import InteropWebBaseScraper

class GradyScraper(InteropWebBaseScraper):
    @property
    def county(self) -> str:
        return "Grady"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://tcsi-roster.azurewebsites.net/default.aspx?code=grady&type=roster&i=44"
