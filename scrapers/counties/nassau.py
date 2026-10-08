"""
Nassau County (FL) — fail closed (portal TLS chain is broken; old parser had
no real booking key).

Source recon (docs/recon/FL_BREVARD_NEWWORLD_2026-10-07.md and
docs/recon/FL_67_STATUS_2026-10-08.md):
  * URL: https://dssinmate.nassauso.com/NewWorld.InmateInquiry/nassau
    (New World InmateInquiry, same contract as Walton / Flagler).
  * The host sends only its leaf certificate (``*.nassauso.com``, issued by
    GoDaddy Secure Certificate Authority - G2); the intermediate is missing,
    so ordinary TLS verification fails (``unable to verify the first
    certificate``; re-checked from the agent box 2026-10-08 07:54 EDT).
  * The previous module turned verification off (``verify=False``) and used
    curl_cffi Chrome impersonation, and it took the ``Booking History``
    heading as the booking number, so every row shared the key ``History``.
  * Owner hold (CoS 2026-10-08): stays closed until the Sheriff's Office
    fixes its chain or the owner approves vendoring the public GoDaddy G2
    intermediate so verification stays on. Then it is a thin
    ``scrapers/fl_newworld.py`` wrapper like Walton / Flagler.
"""
from __future__ import annotations

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

ROSTER_URL = "https://dssinmate.nassauso.com/NewWorld.InmateInquiry/nassau"


class NassauCountyScraper(BaseScraper):
    """Fail closed: incomplete TLS chain; no verified booking-key contract."""

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "dssinmate.nassauso.com serves an incomplete TLS chain (leaf only, "
        "GoDaddy G2 intermediate missing), so ordinary verified HTTPS fails; "
        "the old verify=False parser keyed every row on 'History'. Owner hold "
        "until the chain is fixed or the intermediate is approved."
    )

    @property
    def county(self) -> str:
        return "Nassau"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning("Nassau: SOURCE_CONTRACT_VALIDATED=False — %s", self.SOURCE_CONTRACT_REASON)
        return []
