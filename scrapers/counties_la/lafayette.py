"""Lafayette Parish (LA) source-contract guard.

The 365Labs community portal requires captcha verification. The prior
registered path probed Azure endpoints with TLS verification disabled,
used a headless-browser fallback, and invented ``LAF_`` name-hash booking
keys. This job emits no records and performs no captcha, TLS-bypass,
browser, or synthetic-identifier work.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class LafayetteScraper(BaseScraper):
    """Fail closed until Lafayette has a verified booking-safe public roster."""

    OFFICIAL_SOURCE_URL = (
        "https://lafayettesheriff.com/services/corrections/offender-information/"
    )
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "The 365Labs offender portal is captcha-gated; TLS-disabled endpoint "
        "probes, browser fallback, and name-derived booking fallbacks are not "
        "permitted."
    )

    @property
    def county(self) -> str:
        return "Lafayette"

    @property
    def state(self) -> str:
        return "LA"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "%s %s fail closed: %s",
            self.county,
            self.state,
            self.SOURCE_CONTRACT_REASON,
        )
        return []
