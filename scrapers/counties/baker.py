"""
Baker County (FL) Arrest Scraper — JailTracker.
Source: Baker County Sheriff's Office
Platform: JailTracker (public-safety-cloud.com)
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class BakerCountyScraper(JailTrackerBaseScraper):
    county_jt_id = "Baker_County_FL"
    facility_name = "Baker County Jail"

    @property
    def county(self) -> str:
        return "Baker"

    @property
    def state(self) -> str:
        return "FL"
