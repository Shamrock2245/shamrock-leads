"""Jackson County, AL — fail closed pending a verified official roster contract."""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.southern_sw_base import SouthernSWBaseScraper

logger = logging.getLogger(__name__)


class JacksonALScraper(SouthernSWBaseScraper):
    """Registered guard; configured agency route resolves to the generic directory."""

    SOURCE_CONTRACT_VALIDATED = False

    @property
    def county(self) -> str:
        return "Jackson"

    @property
    def state(self) -> str:
        return "AL"

    @property
    def agency_id(self) -> str:
        return "JacksonCoAL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Jackson AL: fail closed; configured Citizen Connect agency route resolves "
            "to the generic directory and has no verified booking-safe roster contract"
        )
        return []
