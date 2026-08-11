"""
Maury County (TN) Arrest Scraper — JailTracker WASM platform.
URL: https://omsweb.public-safety-cloud.com/jtclientweb/
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class MauryScraper(JailTrackerBaseScraper):
    @property
    def county(self) -> str:
        return "Maury"

    @property
    def state(self) -> str:
        return "TN"

    county_jt_id: str = "Maury_County_TN"
    facility_name: str = "Maury County Sheriff's Department & Jail"
