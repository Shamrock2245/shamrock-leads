"""
Jefferson County (AL) Arrest Scraper — Birmingham metro.

Portal: http://sheriff.jccal.org/NewWorld.InmateInquiry/AL0010000/
Platform: New World InmateInquiry (Tyler Technologies)

Recon 2026-07-20: Returns HTTP 403 from datacenter IPs. Needs APE
residential proxy or curl_cffi TLS fingerprint bypass. Once accessible,
inherit from NewWorldBaseScraper.
"""
from __future__ import annotations

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class JeffersonScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Jefferson"

    @property
    def state(self) -> str:
        return "AL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Jefferson AL: New World portal returns 403 from datacenter IPs. "
            "Needs residential proxy path."
        )
        return []
