"""Livingston Parish (LA) source-contract guard.

The registered path targeted a speculative ``/api/inmates/recent``
endpoint that was never county-validated. It accepted a generic ``id`` as
a booking key and required no booking date/time. This job emits no
records until an official listing contract is validated.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class LivingstonScraper(BaseScraper):
    """Fail closed until Livingston has a verified booking-safe roster."""

    OFFICIAL_SOURCE_URL = "https://www.lpso.org/inmates"
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "The configured /api/inmates/recent path is an unverified speculative "
        "endpoint; no booking-safe official listing contract is validated."
    )

    @property
    def county(self) -> str:
        return "Livingston"

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
