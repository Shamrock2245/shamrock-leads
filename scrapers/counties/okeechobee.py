"""
Okeechobee County (FL) — fail closed (Wix marketing shell; no public roster feed).

Source recon 2026-10-07 (docs/recon/FL_IDLE_EIGHT_2026-10-07.md):
  * URL: https://www.okeesheriff.org/inmate-search is a Wix page with no inmate
    table, JSON feed, or source booking identifier in the HTML.
  * Refuse emission until a plainly accessible roster with source booking ID is proven.
"""
from __future__ import annotations

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.okeesheriff.org/inmate-search"


class OkeechobeeCountyScraper(BaseScraper):
    """Fail closed: Wix inmate-search shell publishes no roster feed."""

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "okeesheriff.org/inmate-search is a Wix shell with no public inmate roster "
        "or source booking identifier."
    )

    @property
    def county(self) -> str:
        return "Okeechobee"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning("Okeechobee: SOURCE_CONTRACT_VALIDATED=False — %s", self.SOURCE_CONTRACT_REASON)
        return []
