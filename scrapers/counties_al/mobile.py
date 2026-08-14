"""Mobile County, Alabama source-safety guard.

The county's public inmate portal has not been validated through normal access as
a broad roster with complete identity, a source-issued booking identifier, and a
booking date/time. Until that contract is observed, this registered path emits
no records. It performs no proxy, residential-IP, CAPTCHA, blank-search,
profile, DOB, or synthetic-identifier work.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class MobileScraper(BaseScraper):
    """Fail closed until Mobile's official public roster contract is verified."""

    OFFICIAL_SOURCE_URL = "https://all.mobileso.com/OthReports/CurrentInmates.aspx"
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "Official current-inmates portal has no verified booking-safe broad roster "
        "contract through normal public access."
    )

    @property
    def county(self) -> str:
        return "Mobile"

    @property
    def state(self) -> str:
        return "AL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "%s %s fail closed: %s",
            self.county,
            self.state,
            self.SOURCE_CONTRACT_REASON,
        )
        return []
