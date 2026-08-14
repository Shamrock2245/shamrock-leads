"""Bell County, Texas source-safety guard.

Bell County's former P2C endpoint is stale. The current official Tyler New World
portal exposes a search form, but no supported broad-list contract has been
validated without guessing person, booking, or date-range criteria. The scraper must
not submit empty or fabricated searches.

The existing registered path therefore fails closed until an official public current
roster with complete identity, source-issued booking identifier, and booking date or
timestamp can be verified.
"""
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper


class BellScraper(BaseScraper):
    """Fail closed while Bell's public New World list contract is unverified."""

    OFFICIAL_INMATE_SEARCH_URL = "https://nwweb.bellcounty.texas.gov/NewWorld.InmateInquiry/TX0140000"

    @property
    def county(self) -> str:
        return "Bell"

    @property
    def state(self) -> str:
        return "TX"

    @property
    def roster_url(self) -> str:
        return self.OFFICIAL_INMATE_SEARCH_URL

    def scrape(self) -> List[ArrestRecord]:
        self.logger.warning(
            "%s %s official portal has no verified broad-list contract; no records emitted pending a supported source-safe roster",
            self.county,
            self.state,
        )
        return []
