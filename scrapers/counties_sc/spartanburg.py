"""
Spartanburg County (SC) Arrest Scraper.

Recon noted 72-hour booking records at southcarolinaofficialrecords.com
(endpoint currently 404). Scaffold until a stable public roster is found.
"""
from __future__ import annotations

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class SpartanburgScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Spartanburg"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Spartanburg SC: prior 72-hour booking URL returns 404. "
            "Needs fresh recon for SO/detention roster."
        )
        return []
