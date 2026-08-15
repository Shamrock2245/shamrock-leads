"""Jefferson County, AL — fail closed pending a verified normal-access roster contract."""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class JeffersonScraper(BaseScraper):
    """Registered safety guard; the configured official portal returns HTTP 403 directly."""

    SOURCE_CONTRACT_VALIDATED = False

    @property
    def county(self) -> str:
        return "Jefferson"

    @property
    def state(self) -> str:
        return "AL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Jefferson AL: fail closed; direct official portal returned HTTP 403 "
            "and no booking-safe normal-access roster contract is verified"
        )
        return []
