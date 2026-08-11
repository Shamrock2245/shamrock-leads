"""
Shelby County (AL) Arrest Scraper — JailTracker WASM platform.
URL: https://omsweb.public-safety-cloud.com/jtclientweb/
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class ShelbyScraper(JailTrackerBaseScraper):
    @property
    def county(self) -> str:
        return "Shelby"

    @property
    def state(self) -> str:
        return "AL"

    county_jt_id: str = "Shelby_County_AL"
    facility_name: str = "Shelby County Detention Center"
