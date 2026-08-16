"""East Baton Rouge Parish (LA) source-contract guard.

The registered path previously used a residential stealth session, a
Cloudflare/disclaimer browser walk, and name-derived ``EBR_`` booking
fallbacks. No official EBRSO listing contract with a complete displayed
name, source-issued booking identifier, and booking date/time has been
validated through ordinary public access. This job emits no records and
performs no proxy, CAPTCHA, browser, or synthetic-identifier work.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class EastBatonRougeScraper(BaseScraper):
    """Fail closed until East Baton Rouge has a verified booking-safe roster."""

    OFFICIAL_SOURCE_URL = "https://www.ebrso.org/resources/prison-inmate-list/"
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "No official EBRSO booking-safe broad roster has been verified through "
        "ordinary public access; stealth, disclaimer-gate browser navigation, "
        "and name-derived booking fallbacks are not permitted."
    )

    @property
    def county(self) -> str:
        return "East Baton Rouge"

    @property
    def state(self) -> str:
        return "LA"

    @property
    def scraper_id(self) -> str:
        return "scraper_la_east_baton_rouge"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "%s %s fail closed: %s",
            self.county,
            self.state,
            self.SOURCE_CONTRACT_REASON,
        )
        return []
