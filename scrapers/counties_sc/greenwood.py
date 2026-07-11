"""
Greenwood County (SC) Arrest Scraper.
Uses JailTrackerBaseScraper.
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper

class GreenwoodScraper(JailTrackerBaseScraper):
    @property
    def county(self) -> str:
        return "Greenwood"
        
    @property
    def portal_url(self) -> str:
        return "https://omsweb.public-safety-cloud.com/jtclientweb/jailtracker/index/Greenwood_County_SC"
