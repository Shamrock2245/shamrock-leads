"""
Columbia County (FL) — fail closed (SmartWEB host dead; no replacement URL).

Source recon 2026-10-07 (docs/recon/FL_IDLE_EIGHT_2026-10-07.md):
  * Legacy URL http://50.204.15.10/smartwebclient/Jail.aspx returns HTTP 503.
  * Official SO site https://columbiasheriff.org/ has no working inmate-search
    deep link (FAQ page 404; detention pages only reference SmartInmate mail).
  * Florida DOS county-jail directory lists Columbia with no inmate-search URL.
  * Refuse emission until a plainly accessible roster with source Booking No is proven.
"""
from __future__ import annotations

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

SEARCH_URL = "http://50.204.15.10/smartwebclient/Jail.aspx"


class ColumbiaCountyScraper(BaseScraper):
    """Fail closed: legacy SmartWEB IP is 503; no public replacement roster URL."""

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "Columbia FL SmartWEB host 50.204.15.10 returns 503; columbiasheriff.org "
        "has no working public inmate-search URL. No invented portal."
    )

    @property
    def county(self) -> str:
        return "Columbia"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning("Columbia: SOURCE_CONTRACT_VALIDATED=False — %s", self.SOURCE_CONTRACT_REASON)
        return []
