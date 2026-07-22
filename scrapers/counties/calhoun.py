"""
Calhoun County (FL) Arrest Scraper — JailTracker.
Source: Calhoun County Sheriff's Office
Platform: JailTracker (public-safety-cloud.com)
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class CalhounCountyScraper(JailTrackerBaseScraper):
    county_jt_id = "Calhoun_County_FL"
    facility_name = "Calhoun County Jail"

    @property
    def county(self) -> str:
        return "Calhoun"

    @property
    def state(self) -> str:
        return "FL"
