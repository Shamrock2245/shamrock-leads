"""
Marion County (SC) — fail closed.

No booking-safe public roster contract is verified for Marion County, SC.
This module used to fetch ``jail.marionso.com``, which is the Marion County
(FL) Sheriff's jail host, not South Carolina's. It also had an optional
SOCKS/residential proxy path and invented booking keys (name hash) and "0"
bonds. All of that is gone (2026-10-15 cleanup). No source URL is configured
and no replacement is guessed. A future reopen needs a verified official
Marion County (SC) roster that publishes a source booking number.
"""
from __future__ import annotations

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class MarionScraper(BaseScraper):

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "No verified official Marion County (SC) booking roster contract; the "
        "previous host was the Florida Marion jail site and has been removed. "
        "No source request is made."
    )

    @property
    def county(self) -> str:
        return "Marion"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning("Marion (SC): SOURCE_CONTRACT_VALIDATED=False — %s", self.SOURCE_CONTRACT_REASON)
        return []
