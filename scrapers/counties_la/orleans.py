"""Orleans Parish (LA) source-contract guard.

The OPSO public origin is reachable, but it did not establish a broad public
booking roster with a complete listing name, source-issued immutable identifier,
booking timestamp, and bounded pagination through ordinary access. The prior
implementation also attempted speculative endpoints, browser navigation, and
name-derived booking fallbacks. This registered path deliberately emits no
records until a county-specific contract validation supports a source-faithful
listing parser.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class OrleansScraper(BaseScraper):
    """Fail closed until Orleans' public booking-safe roster contract is verified."""

    OFFICIAL_SOURCE_URL = "https://www.opso.gov"
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "The reachable OPSO public origin did not establish a compliant broad "
        "booking roster through ordinary access; speculative endpoints, browser "
        "navigation, and synthetic booking fallbacks are not permitted."
    )

    @property
    def county(self) -> str:
        return "Orleans"

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
