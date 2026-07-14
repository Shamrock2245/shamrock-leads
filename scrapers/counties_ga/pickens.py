"""
Pickens County (GA) Arrest Scraper.
Uses JailTrackerBaseScraper.
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper

class PickensScraper(JailTrackerBaseScraper):
    @property
    def county(self) -> str:
        return "Pickens"
        
    @property
    def state(self) -> str:
        return "GA"

    @property
    def portal_url(self) -> str:
        return "https://omsweb.public-safety-cloud.com/jtclientweb/jailtracker/index/Pickens_County_GA"
