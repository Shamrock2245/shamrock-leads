"""
Gulf County (FL) Arrest Scraper — JailTracker.
Source: Gulf County Sheriff's Office
Platform: JailTracker (public-safety-cloud.com)
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class GulfCountyScraper(JailTrackerBaseScraper):
    county_jt_id = "Gulf_County_FL"
    facility_name = "Gulf County Jail"

    @property
    def county(self) -> str:
        return "Gulf"

    @property
    def state(self) -> str:
        return "FL"
