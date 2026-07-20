"""
Madison County (AL) Arrest Scraper — Huntsville metro.

Portal: https://www.madisoncountyal.gov/departments/sheriff/inmate-information
Platform: CivicPlus (likely iframe or external redirect)

Recon 2026-07-20: Returns HTTP 403 from datacenter IPs. Needs deeper
investigation to find the actual inmate roster endpoint.
"""
from __future__ import annotations

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class MadisonScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Madison"

    @property
    def state(self) -> str:
        return "AL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Madison AL: county portal returns 403 from datacenter IPs. "
            "Needs residential proxy or deeper recon."
        )
        return []
