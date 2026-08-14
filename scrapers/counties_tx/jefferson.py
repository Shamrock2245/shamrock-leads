"""Jefferson County, Texas source-safety guard.

Jefferson County's former P2C endpoint is stale. The current official sheriff page
is a public Next.js application, but normal aggregate inspection did not establish
a supported bulk roster contract containing complete identity, a source-issued
booking or inmate identifier, and a booking date or timestamp. Opaque detail routes
and current-inmate/current-arrest PDF links are not a substitute for a verified
bulk ingest contract.

The registered path therefore fails closed until a durable official public roster
contract can be verified without detail-page collection or identifier probing.
"""
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper


class JeffersonScraper(BaseScraper):
    """Fail closed while Jefferson's public bulk-roster contract is unverified."""

    OFFICIAL_INMATE_SEARCH_URL = "https://www.sheriff.jeffersoncountytx.gov/inmateSearch"

    @property
    def county(self) -> str:
        return "Jefferson"

    @property
    def state(self) -> str:
        return "TX"

    @property
    def roster_url(self) -> str:
        return self.OFFICIAL_INMATE_SEARCH_URL

    def scrape(self) -> List[ArrestRecord]:
        self.logger.warning(
            "%s %s official source has no verified booking-safe bulk roster contract; no records emitted pending a supported source-safe roster",
            self.county,
            self.state,
        )
        return []
