"""Montgomery County, Alabama source-safety guard.

The county's public inmate API currently returns HTTP 403 to normal access, so a
broad booking-safe public contract with complete identity, source-issued booking
identifier, and booking date/time has not been established. Until that changes,
this registered path emits no records and performs no proxy, CAPTCHA, search,
profile, or identifier-guessing work.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class MontgomeryScraper(BaseScraper):
    """Fail closed until Montgomery's official public API contract is verified."""

    OFFICIAL_SOURCE_URL = "https://www.mc-ala.org/api/v1/inmates"
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "Official public inmates API returned HTTP 403 through normal access; no "
        "booking-safe broad roster contract is verified."
    )

    @property
    def county(self) -> str:
        return "Montgomery"

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
