"""Calcasieu Parish (LA) source-contract guard.

The prior configured ``/api/inmates/roster`` endpoint now resolves to the public
site's 404 surface. Although the current roster page is publicly reachable, its
live API contract, complete listing-name field, source-issued inmate identifier,
booking timestamp, and bounded pagination require fresh county-specific
validation. This registered path therefore emits no records and performs no
proxy, CAPTCHA, detail-page, or synthetic-identifier work.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class CalcasieuScraper(BaseScraper):
    """Fail closed until Calcasieu's current public roster contract is revalidated."""

    OFFICIAL_SOURCE_URL = "https://www.cpso.com/inmateRoster"
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "The prior /api/inmates/roster endpoint returned HTTP 404 through normal "
        "public access; the current roster API and booking-safe listing contract "
        "are not yet revalidated."
    )

    @property
    def county(self) -> str:
        return "Calcasieu"

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
