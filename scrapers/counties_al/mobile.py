"""
Mobile County (AL) Arrest Scraper.

Portal: https://all.mobileso.com/OthReports/CurrentInmates.aspx
(embedded via iframe at https://www.mobileso.com/whos-in-jail/)
Platform: Custom ASP.NET

Recon 2026-07-20: Returns HTTP 403 from datacenter IPs. Needs APE
residential proxy or curl_cffi TLS fingerprint bypass.
"""
from __future__ import annotations

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class MobileScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Mobile"

    @property
    def state(self) -> str:
        return "AL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Mobile AL: CurrentInmates.aspx returns 403 from datacenter IPs. "
            "Needs residential proxy path."
        )
        return []
