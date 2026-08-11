"""
Blount County (TN) Arrest Scraper — JailTracker WASM platform.
URL: https://omsweb.public-safety-cloud.com/jtclientweb/
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class BlountScraper(JailTrackerBaseScraper):
    @property
    def county(self) -> str:
        return "Blount"

    @property
    def state(self) -> str:
        return "TN"

    county_jt_id: str = "Blount_County_TN"
    facility_name: str = "Blount County Detention Facility"
