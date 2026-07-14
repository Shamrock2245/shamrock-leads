"""
Calhoun County (SC) Arrest Scraper.

NOTE: Recon URL jailroster.kologik.com/?_fl0070000 resolves to
**Calhoun County FL** (Blountstown), NOT South Carolina.
SC Calhoun currently has no confirmed public roster.
"""
from __future__ import annotations

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class CalhounScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Calhoun"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Calhoun SC: no verified public roster. "
            "Kologik FL0070000 is Calhoun FL (Blountstown) — do not use."
        )
        return []
