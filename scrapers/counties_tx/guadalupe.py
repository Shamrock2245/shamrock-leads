"""Guadalupe County, Texas source-safety guard.

Guadalupe County's former P2C endpoint is stale. The county-linked Tyler Public
Access portal loads, but its Jail Records route requires human verification before
any public search or roster content is available. No verification challenge may be
bypassed.

The registered path therefore fails closed until the county provides a supported
public broad roster with complete identity, a source-issued booking or inmate
identifier, and a booking date or timestamp.
"""
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper


class GuadalupeScraper(BaseScraper):
    """Fail closed while Guadalupe's public jail source is verification-protected."""

    OFFICIAL_JAIL_RECORDS_URL = "https://portal-txguadalupe.tylertech.cloud/PublicAccess/JailingSearch.aspx?ID=600"

    @property
    def county(self) -> str:
        return "Guadalupe"

    @property
    def state(self) -> str:
        return "TX"

    @property
    def roster_url(self) -> str:
        return self.OFFICIAL_JAIL_RECORDS_URL

    def scrape(self) -> List[ArrestRecord]:
        self.logger.warning(
            "%s %s official jail route is human-verification protected; no records emitted pending a supported source-safe roster",
            self.county,
            self.state,
        )
        return []
