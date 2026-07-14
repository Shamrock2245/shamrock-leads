"""
Chester County (SC) Arrest Scraper — JailTracker.
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class ChesterScraper(JailTrackerBaseScraper):
    county_jt_id = "Chester_County_SC"

    @property
    def county(self) -> str:
        return "Chester"

    @property
    def state(self) -> str:
        return "SC"
