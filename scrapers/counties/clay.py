"""
Clay County (FL) — fail closed (public detention listing has no source booking ID).

Source recon 2026-10-07 (docs/recon/FL_IDLE_EIGHT_2026-10-07.md):
  * URL: https://www.sheriffclayco.org/divisions/detention/detention-listings/
  * Plain HTTPS returns a public HTML table (~400 rows) with Name / Booking Date /
    Court / Court Date. No Booking # / inmate ID column is published.
  * Without a source-issued booking identifier we refuse emission rather than
    keying on name (which invents uniqueness and duplicates).
"""
from __future__ import annotations

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.sheriffclayco.org/divisions/detention/detention-listings/"


class ClayCountyScraper(BaseScraper):
    """Fail closed: public roster has no source booking number."""

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "sheriffclayco.org detention-listings is a public Name/Booking Date table "
        "with no source booking number; refuse emission until a source ID appears."
    )

    @property
    def county(self) -> str:
        return "Clay"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning("Clay: SOURCE_CONTRACT_VALIDATED=False — %s", self.SOURCE_CONTRACT_REASON)
        return []
