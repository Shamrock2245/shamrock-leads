"""Broward County, Florida source-safety guard.

The prior implementation sequentially probed inmate-detail identifiers, used browser
impersonation with disabled TLS verification, retained DOB, and assumed custody
status. Broward's official public arrest application is now Turnstile-protected, and
its broad booking-date/time contract cannot be validated without interacting with an
access control.

The registered path therefore fails closed until the Sheriff's Office publishes a
supported public bulk roster with complete identity, source-issued booking identifier,
and booking date or timestamp.
"""
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper


class BrowardCountyScraper(BaseScraper):
    """Fail closed while Broward's public source contract is access-restricted."""

    OFFICIAL_ARREST_SEARCH_URL = "https://apps.sheriff.org/arrestsearch"

    @property
    def county(self) -> str:
        return "Broward"

    @property
    def roster_url(self) -> str:
        return self.OFFICIAL_ARREST_SEARCH_URL

    def scrape(self) -> List[ArrestRecord]:
        self.logger.warning(
            "%s public source is Turnstile-protected with no verified booking-safe bulk contract; no records emitted",
            self.county,
        )
        return []
