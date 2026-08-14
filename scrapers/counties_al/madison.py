"""Madison County, Alabama source-safety guard.

The county's official inmate-information surface has not yielded a verified public
broad roster containing complete identity, a source-issued booking identifier,
and a booking date/time through normal access. Until that contract is observed,
this registered path deliberately emits no records. It performs no proxy,
residential-IP, CAPTCHA, blank-search, profile, or synthetic-identifier work.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class MadisonScraper(BaseScraper):
    """Fail closed until Madison's official public roster contract is verified."""

    OFFICIAL_SOURCE_URL = (
        "https://www.madisoncountyal.gov/departments/sheriff/inmate-information"
    )
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "Official inmate-information page has no verified booking-safe broad roster "
        "contract through normal public access."
    )

    @property
    def county(self) -> str:
        return "Madison"

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
