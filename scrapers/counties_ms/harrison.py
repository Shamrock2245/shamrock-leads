"""Harrison County, MS — fail closed pending a verified official API contract."""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class HarrisonScraper(BaseScraper):
    """Registered safety guard; emits no records until the official source is verified."""

    SOURCE_CONTRACT_VALIDATED = False

    @property
    def county(self) -> str:
        return "Harrison"

    @property
    def state(self) -> str:
        return "MS"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Harrison MS: fail closed; configured API has no verified parseable "
            "listing-only booking-safe identity contract"
        )
        return []
