"""Sarasota County, Florida source-safety scraper.

The prior implementation relied on a third-party booking mirror and attempted
residential-proxy, CAPTCHA, browser, profile, DOB, mugshot, and synthetic-ID
fallbacks against official surfaces. None provides a currently verified,
booking-safe broad official roster contract through normal public access.

The county remains registered for operational visibility but intentionally emits
no records until a source-faithful public contract is validated.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.jailtracker_base import JailTrackerBaseScraper

logger = logging.getLogger(__name__)


class SarasotaCountyScraper(JailTrackerBaseScraper):
    """Fail closed pending an official Sarasota booking-safe roster contract."""

    county_jt_id = "SARASOTA_COUNTY_FL"
    facility_name = "Sarasota County Jail"
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "No official Sarasota broad roster with complete identity, source-issued "
        "booking identity, and booking timestamp is verified through normal access."
    )

    @property
    def county(self) -> str:
        return "Sarasota"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Sarasota FL fails closed: %s", self.SOURCE_CONTRACT_REASON
        )
        return []
