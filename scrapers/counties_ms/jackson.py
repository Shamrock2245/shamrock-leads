"""Jackson County, MS — fail closed pending a verified normal-access roster contract."""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class JacksonScraper(BaseScraper):
    """Registered safety guard; emits no records until an official public contract is verified."""

    SOURCE_CONTRACT_VALIDATED = False

    @property
    def county(self) -> str:
        return "Jackson"

    @property
    def state(self) -> str:
        return "MS"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Jackson MS: fail closed; direct official source returned access control "
            "and no booking-safe normal-access roster contract is verified"
        )
        return []
