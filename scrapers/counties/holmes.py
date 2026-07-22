"""
Holmes County (FL) Arrest Scraper — JailTracker.
Source: Holmes County Sheriff's Office
Platform: JailTracker (public-safety-cloud.com)
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class HolmesCountyScraper(JailTrackerBaseScraper):
    county_jt_id = "Holmes_County_FL"
    facility_name = "Holmes County Jail"

    @property
    def county(self) -> str:
        return "Holmes"

    @property
    def state(self) -> str:
        return "FL"
