"""St. Tammany Parish (LA) source-contract guard.

The prior configured ``/api/inmates/recent`` endpoint returned HTTP 403 through
ordinary public access. No public broad-listing contract with complete identity,
a source-issued immutable booking identifier, booking time, and bounded
pagination is currently verified. This registered path therefore emits no
records and performs no proxy, CAPTCHA, profile, or synthetic-identifier work.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class StTammanyScraper(BaseScraper):
    """Fail closed until St. Tammany's official roster contract is revalidated."""

    OFFICIAL_SOURCE_URL = "https://www.stpso.com/inmate-search"
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "The prior /api/inmates/recent endpoint returned HTTP 403 through normal "
        "public access; no booking-safe broad roster contract is revalidated."
    )

    @property
    def county(self) -> str:
        return "St. Tammany"

    @property
    def state(self) -> str:
        return "LA"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "%s %s fail closed: %s",
            self.county,
            self.state,
            self.SOURCE_CONTRACT_REASON,
        )
        return []
