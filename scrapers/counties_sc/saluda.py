"""
Saluda County (SC) Arrest Scraper.

No verified public machine-readable jail roster (see docs/SC_RECON_RESULTS.md).
Explicit empty scraper so the county is registered and monitored.
"""
from __future__ import annotations

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class SaludaScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Saluda"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        logger.info("Saluda SC: no public roster — returning empty")
        return []
