"""Clermont County, Ohio source-contract safety guard.

A public sheriff roster was observed during passive reconnaissance, but the repository
has no county-specific authorization/terms review, field allowlist, retention approval,
or verified normal-access source contract. This module is registered only so the scope is
visible as guarded. It emits no records and performs no source request until a separate,
reviewed promotion change proves the required contract.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class ClermontScraper(BaseScraper):
    """Fail closed until Clermont's public roster contract is explicitly approved."""

    OFFICIAL_SOURCE_URL = "https://www.clermontsheriff.org/jail-inmate-search/"
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "Ohio pilot guard: no county-specific approved public source contract, "
        "field allowlist, or records-use review is documented."
    )

    @property
    def county(self) -> str:
        return "Clermont"

    @property
    def state(self) -> str:
        return "OH"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning("%s %s fail closed: %s", self.county, self.state, self.SOURCE_CONTRACT_REASON)
        return []
