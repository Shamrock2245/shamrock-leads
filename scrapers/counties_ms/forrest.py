"""Forrest County, MS — fail closed pending a verified official API contract."""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class ForrestScraper(BaseScraper):
    """Registered safety guard; emits no records until the official source is verified."""

    SOURCE_CONTRACT_VALIDATED = False

    @property
    def county(self) -> str:
        return "Forrest"

    @property
    def state(self) -> str:
        return "MS"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Forrest MS: fail closed; configured official API returned 404 and no "
            "booking-safe public contract is currently verified"
        )
        return []
