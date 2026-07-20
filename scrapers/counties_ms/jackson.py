"""
Jackson County (MS) Arrest Scraper.

Portal: https://www.co.jackson.ms.us/324/Inmate-Lookup
API: https://services.co.jackson.ms.us/inmatedocket/_inmateList.php?Function=list&Page=1&Order=BookDesc
Platform: Custom PHP + jQuery (CivicPlus host)

Recon 2026-07-20: The API endpoint is behind Cloudflare managed challenge.
Needs residential proxy (APE) or curl_cffi TLS fingerprint to bypass.
The parent page on co.jackson.ms.us loads fine, but the services subdomain
triggers Cloudflare. Scaffold until APE residential pool is active.
"""
from __future__ import annotations

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)


class JacksonScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Jackson"

    @property
    def state(self) -> str:
        return "MS"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Jackson MS: API behind Cloudflare managed challenge. "
            "Needs APE residential proxy or curl_cffi path."
        )
        return []
