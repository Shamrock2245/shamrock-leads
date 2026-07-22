"""
Washington County (FL) Arrest Scraper — JailTracker.
Source: Washington County Sheriff's Office
Platform: JailTracker (public-safety-cloud.com)
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class WashingtonCountyScraper(JailTrackerBaseScraper):
    county_jt_id = "Washington_County_FL"
    facility_name = "Washington County Jail"

    @property
    def county(self) -> str:
        return "Washington"

    @property
    def state(self) -> str:
        return "FL"
