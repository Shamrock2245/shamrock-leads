"""
Chester County (SC) Arrest Scraper.
Uses JailTrackerBaseScraper.
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper

class ChesterScraper(JailTrackerBaseScraper):
    @property
    def county(self) -> str:
        return "Chester"
        
    @property
    def portal_url(self) -> str:
        return "https://omsweb.public-safety-cloud.com/jtclientweb/jailtracker/index/Chester_County_SC"
