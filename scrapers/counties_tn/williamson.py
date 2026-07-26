"""
Williamson County (TN) Arrest Scraper — JailTracker.

Portal: JailTracker public-safety-cloud (Williamson County Detention).
JT ID: Williamson_County_TN
"""
from __future__ import annotations

from scrapers.jailtracker_base import JailTrackerBaseScraper


class WilliamsonScraper(JailTrackerBaseScraper):
    county_jt_id = "Williamson_County_TN"
    facility_name = "Williamson County Jail"

    @property
    def county(self) -> str:
        return "Williamson"

    @property
    def state(self) -> str:
        return "TN"
