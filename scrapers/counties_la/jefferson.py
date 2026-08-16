"""Jefferson Parish (LA) source-contract guard.

The registered path previously used curl_cffi/StealthSession TLS
fingerprinting, a headless-browser fallback, and name-derived ``JEF_``
booking fallbacks. No official JPSO InmateSearch listing contract with a
complete displayed name, source-issued booking identifier, and booking
date/time has been validated through ordinary public access. This job
emits no records and performs no proxy, TLS-bypass, browser, or
synthetic-identifier work.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class JeffersonScraper(BaseScraper):
    """Fail closed until Jefferson Parish has a verified booking-safe roster."""

    OFFICIAL_SOURCE_URL = "https://apps.jpso.com/inmatesearch/"
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "No official JPSO booking-safe broad roster has been verified through "
        "ordinary public access; stealth TLS fingerprinting, browser fallback, "
        "and name-derived booking fallbacks are not permitted."
    )

    @property
    def county(self) -> str:
        return "Jefferson"

    @property
    def state(self) -> str:
        return "LA"

    @property
    def scraper_id(self) -> str:
        return "scraper_la_jefferson"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "%s %s fail closed: %s",
            self.county,
            self.state,
            self.SOURCE_CONTRACT_REASON,
        )
        return []
