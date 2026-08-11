"""
Etowah County (AL) Arrest Scraper — JailTracker WASM platform.
URL: https://omsweb.public-safety-cloud.com/jtclientweb/
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class EtowahScraper(JailTrackerBaseScraper):
    @property
    def county(self) -> str:
        return "Etowah"

    @property
    def state(self) -> str:
        return "AL"

    county_jt_id: str = "Etowah_County_AL"
    facility_name: str = "Etowah County Detention Center"
