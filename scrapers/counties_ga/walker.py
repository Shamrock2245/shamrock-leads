"""
Walker County (GA) Arrest Scraper — JailTracker WASM platform.
URL: https://omsweb.public-safety-cloud.com/jtclientweb/
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class WalkerScraper(JailTrackerBaseScraper):
    @property
    def county(self) -> str:
        return "Walker"

    @property
    def state(self) -> str:
        return "GA"

    county_jt_id: str = "Walker_County_GA"
    facility_name: str = "Walker County Detention Center"
