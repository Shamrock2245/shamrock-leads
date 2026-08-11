"""
Madison County (MS) Arrest Scraper — JailTracker WASM platform.
URL: https://omsweb.public-safety-cloud.com/jtclientweb/
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class MadisonMSScraper(JailTrackerBaseScraper):
    @property
    def county(self) -> str:
        return "Madison"

    @property
    def state(self) -> str:
        return "MS"

    county_jt_id: str = "Madison_County_MS"
    facility_name: str = "Madison County Detention Center"
