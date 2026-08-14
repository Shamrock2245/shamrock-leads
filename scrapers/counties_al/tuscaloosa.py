"""Tuscaloosa County, Alabama source-safety guard.

The prior implementation pointed to ``tcso.org``, which is Tulsa County,
Oklahoma, rather than Tuscaloosa County, Alabama. Tuscaloosa's official
Sheriff site directs users to a human-verification-protected, person-search
"Who's in Jail" surface. It does not presently provide a verified broad,
booking-safe contract through normal automated access.

The registered job remains visible for operational monitoring but intentionally
emits no records until the Sheriff publishes a normal-access public roster that
includes complete identity, a source-issued booking identifier, and booking
date/time.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class TuscaloosaScraper(BaseScraper):
    """Fail closed until a Tuscaloosa official public roster is validated."""

    OFFICIAL_SOURCE_URL = "https://www.tcsoal.org/inmates"
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "Official Tuscaloosa Sheriff's 'Who's in Jail' surface requires human "
        "verification and no broad booking-safe public roster contract is "
        "verified through normal access."
    )

    @property
    def county(self) -> str:
        return "Tuscaloosa"

    @property
    def state(self) -> str:
        return "AL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "%s %s fails closed: %s",
            self.county,
            self.state,
            self.SOURCE_CONTRACT_REASON,
        )
        return []
