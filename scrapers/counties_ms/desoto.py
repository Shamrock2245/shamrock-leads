"""
DeSoto County (MS) Arrest Scraper — JailTracker WASM platform.
URL: https://omsweb.public-safety-cloud.com/jtclientweb/
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class DeSotoScraper(JailTrackerBaseScraper):
    @property
    def county(self) -> str:
        return "DeSoto"

    @property
    def state(self) -> str:
        return "MS"

    county_jt_id: str = "DeSoto_County_MS"
    facility_name: str = "DeSoto County Adult Detention Center"
