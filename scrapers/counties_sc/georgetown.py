"""
Georgetown County (SC) Arrest Scraper.

Recon (2026-07): gcsheriff.org booking page has no scrapeable live roster —
primarily static content + VINE referrals. Marked scaffold until a public
feed is confirmed.
"""
from __future__ import annotations

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class GeorgetownScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Georgetown"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Georgetown SC: no public machine-readable roster found "
            "(VINE / static sheriff pages only). Returning empty."
        )
        return []
