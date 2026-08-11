"""
Wilson County (TN) Arrest Scraper — JailTracker WASM / REST platform.
URL: https://omsweb.public-safety-cloud.com/jtclientweb/
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class WilsonScraper(JailTrackerBaseScraper):
    @property
    def county(self) -> str:
        return "Wilson"

    @property
    def state(self) -> str:
        return "TN"

    county_jt_id: str = "Wilson_County_TN"
    facility_name: str = "Wilson County Jail"
