"""
Levy County (FL) Arrest Scraper — JailTracker.
Source: Levy County Sheriff's Office
Platform: JailTracker (public-safety-cloud.com)
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class LevyCountyScraper(JailTrackerBaseScraper):
    county_jt_id = "Levy_County_FL"
    facility_name = "Levy County Detention Facility"

    @property
    def county(self) -> str:
        return "Levy"

    @property
    def state(self) -> str:
        return "FL"
