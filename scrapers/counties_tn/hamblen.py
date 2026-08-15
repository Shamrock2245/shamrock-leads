"""Hamblen County, TN — fail closed pending a verified official roster contract."""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.zuercher_base import ZuercherBaseScraper

logger = logging.getLogger(__name__)


class HamblenScraper(ZuercherBaseScraper):
    """Registered guard; stale portal and public fallback lack a booking-safe identity contract."""

    SOURCE_CONTRACT_VALIDATED = False

    @property
    def county(self) -> str:
        return "Hamblen"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def portal_url(self) -> str:
        return "https://hamblensheriff.zuercherportal.com"

    @property
    def default_facility(self) -> str:
        return "Hamblen County Jail"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Hamblen TN: fail closed; configured Zuercher portal is stale and the public "
            "ISOMS listing has no verified booking-safe identity contract"
        )
        return []
