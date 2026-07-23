"""
Rutherford County (TN) Arrest Scraper — JailTracker (public-safety-cloud.com).

Rutherford County uses the standard JailTracker Blazor WASM inmate roster.
JailTracker ID: Rutherford_County_TN
Facility: Rutherford County Adult Detention Center (956 beds)
Address: 940 New Salem Hwy, Murfreesboro, TN 37129
Phone: 615-898-7777

Inherits all CAPTCHA solving, session management, and roster parsing
from JailTrackerBaseScraper.
"""
from scrapers.jailtracker_base import JailTrackerBaseScraper


class RutherfordScraper(JailTrackerBaseScraper):
    """Rutherford County (TN) — JailTracker Blazor WASM roster."""

    county_jt_id = "Rutherford_County_TN"
    facility_name = "Rutherford County Adult Detention Center"

    @property
    def county(self) -> str:
        return "Rutherford"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def scraper_id(self) -> str:
        return "scraper_tn_rutherford"
