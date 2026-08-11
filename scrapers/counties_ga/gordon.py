"""
Gordon County (GA) Arrest Scraper — JailTracker WASM platform.
URL: https://omsweb.public-safety-cloud.com/jtclientweb/
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class GordonScraper(JailTrackerBaseScraper):
    @property
    def county(self) -> str:
        return "Gordon"

    @property
    def state(self) -> str:
        return "GA"

    county_jt_id: str = "Gordon_County_GA"
    facility_name: str = "Gordon County Jail"
