"""
Whitfield County (GA) Arrest Scraper — JailTracker WASM platform.
URL: https://omsweb.public-safety-cloud.com/jtclientweb/
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class WhitfieldScraper(JailTrackerBaseScraper):
    @property
    def county(self) -> str:
        return "Whitfield"

    @property
    def state(self) -> str:
        return "GA"

    county_jt_id: str = "Whitfield_County_GA"
    facility_name: str = "Whitfield County Correctional Center"
