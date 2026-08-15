"""Houston County, AL — fail closed pending a verified normal-access roster contract."""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.southern_sw_base import SouthernSWBaseScraper

logger = logging.getLogger(__name__)


class HoustonScraper(SouthernSWBaseScraper):
    """Registered guard; the configured public source is inaccessible to normal requests."""

    SOURCE_CONTRACT_VALIDATED = False

    @property
    def county(self) -> str:
        return "Houston"

    @property
    def state(self) -> str:
        return "AL"

    @property
    def agency_id(self) -> str:
        return "HoustonCoAL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Houston AL: fail closed; direct public source access returned HTTP 403 "
            "and no booking-safe normal-access roster contract is verified"
        )
        return []
