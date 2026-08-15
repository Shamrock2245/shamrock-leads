"""Bradley County, TN — fail closed pending a verified official roster contract."""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.southern_sw_base import SouthernSWBaseScraper

logger = logging.getLogger(__name__)


class BradleyScraper(SouthernSWBaseScraper):
    """Registered guard; configured agency route resolves to the generic directory."""

    SOURCE_CONTRACT_VALIDATED = False

    @property
    def county(self) -> str:
        return "Bradley"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def agency_id(self) -> str:
        return "BradleyCoTN"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Bradley TN: fail closed; configured Citizen Connect agency route resolves "
            "to the generic directory and has no verified booking-safe roster contract"
        )
        return []
