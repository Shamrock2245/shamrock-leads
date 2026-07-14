"""
Greenwood County (SC) Arrest Scraper — JailTracker.
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class GreenwoodScraper(JailTrackerBaseScraper):
    county_jt_id = "Greenwood_County_SC"

    @property
    def county(self) -> str:
        return "Greenwood"

    @property
    def state(self) -> str:
        return "SC"
