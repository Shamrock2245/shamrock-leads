"""Hinds County, MS — fail closed pending a verified official listing contract."""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class HindsScraper(BaseScraper):
    """Registered safety guard; emits no records until a listing-only contract is proven."""

    SOURCE_CONTRACT_VALIDATED = False

    @property
    def county(self) -> str:
        return "Hinds"

    @property
    def state(self) -> str:
        return "MS"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Hinds MS: fail closed; official public listing has no currently verified "
            "listing-only booking-safe identity contract"
        )
        return []
