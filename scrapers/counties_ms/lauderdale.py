"""
Lauderdale County (MS) Arrest Scraper — JailTracker WASM platform.
URL: https://omsweb.public-safety-cloud.com/jtclientweb/
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class LauderdaleScraper(JailTrackerBaseScraper):
    @property
    def county(self) -> str:
        return "Lauderdale"

    @property
    def state(self) -> str:
        return "MS"

    county_jt_id: str = "Lauderdale_County_MS"
    facility_name: str = "Lauderdale County Detention Facility"
