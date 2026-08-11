"""
Jones County (MS) Arrest Scraper — JailTracker WASM platform.
URL: https://omsweb.public-safety-cloud.com/jtclientweb/
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class JonesScraper(JailTrackerBaseScraper):
    @property
    def county(self) -> str:
        return "Jones"

    @property
    def state(self) -> str:
        return "MS"

    county_jt_id: str = "Jones_County_MS"
    facility_name: str = "Jones County Adult Detention Center"
