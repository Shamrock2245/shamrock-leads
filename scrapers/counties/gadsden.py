"""Gadsden County (FL) — fail closed (embedded SmartWEB host unreachable).

2026-09-25 recon (docs/recon/FL_GAP_QUEUE_2026-09-25.md): the official
https://gadsdensheriff.com/inmate-lookup/ page (HTTP 200) contains no roster
table; it only embeds an iframe to ``http://69.21.72.195/smartwebclient/`` (a
SmartCOP SmartWEB client on a bare IP, plain HTTP). From the box that host
accepts the TCP connection but never answers (HTTP timeout with 0 bytes; HTTPS
TLS EOF), so no source contract, row or booking-number format could be
verified. The previous parser scraped the WordPress page, which never contains
roster rows, and then fell back to a browser.

Reopen only after the SmartWEB JAIL View is reachable and a supported broad
criterion (e.g. booking-date window, as for Suwannee) returns source
``Booking No`` values.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

ROSTER_URL = "https://gadsdensheriff.com/inmate-lookup/"
SMARTWEB_URL = "http://69.21.72.195/smartwebclient/"


class GadsdenCountyScraper(BaseScraper):
    """Fail closed: official page only iframes an unreachable bare-IP SmartWEB host."""

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "Official inmate-lookup page only iframes http://69.21.72.195/smartwebclient/, "
        "which does not respond to box egress; no roster rows or booking numbers verified."
    )

    @property
    def county(self) -> str:
        return "Gadsden"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning("Gadsden FL fails closed: %s", self.SOURCE_CONTRACT_REASON)
        return []
