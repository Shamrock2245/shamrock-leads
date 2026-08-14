"""JailTracker source-safety base.

JailTracker's current public configuration requires a human-verification CAPTCHA
before exposing an offender roster. This base deliberately performs no CAPTCHA
solving, OCR, proxying, automated form submission, browser harvesting, profile
collection, sensitive-field collection, or synthetic identifier construction.

Dependent county wrappers remain registered for operational visibility but emit
no records until each county's official public source has a separately verified
broad roster contract containing complete identity, a source-issued booking or
inmate identifier, and a booking date/time.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

JT_BASE = "https://omsweb.public-safety-cloud.com/jtclientweb"


class JailTrackerBaseScraper(BaseScraper):
    """Fail closed for JailTracker paths without a verified public roster contract."""

    county_jt_id: str = ""
    facility_name: str = ""
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "Official JailTracker roster requires human verification and no booking-safe "
        "broad public roster contract is verified through normal access."
    )

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "%s %s JailTracker path fails closed: %s",
            self.county,
            getattr(self, "state", ""),
            self.SOURCE_CONTRACT_REASON,
        )
        return []
