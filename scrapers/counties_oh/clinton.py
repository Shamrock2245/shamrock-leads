"""Clinton County, Ohio source-contract safety guard.

Passive reconnaissance identified a public active-inmate roster, but no county-specific
approval exists for automated collection, field retention, or operational use. The guard
keeps the scope visible without contacting the source, emitting records, or activating any
downstream Shamrock workflow.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class ClintonScraper(BaseScraper):
    """Fail closed until Clinton's public roster contract is explicitly approved."""

    OFFICIAL_SOURCE_URL = "https://clintonsheriff.com/active-inmates/"
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "Ohio pilot guard: no county-specific approved public source contract, "
        "field allowlist, or records-use review is documented."
    )

    @property
    def county(self) -> str:
        return "Clinton"

    @property
    def state(self) -> str:
        return "OH"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning("%s %s fail closed: %s", self.county, self.state, self.SOURCE_CONTRACT_REASON)
        return []
