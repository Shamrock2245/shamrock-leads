"""
Wakulla County (FL) Arrest Scraper — JailTracker.
Source: Wakulla County Sheriff's Office
Platform: JailTracker (public-safety-cloud.com)
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class WakullaCountyScraper(JailTrackerBaseScraper):
    county_jt_id = "Wakulla_County_FL"
    facility_name = "Wakulla County Jail"

    @property
    def county(self) -> str:
        return "Wakulla"

    @property
    def state(self) -> str:
        return "FL"
