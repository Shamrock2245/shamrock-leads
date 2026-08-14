"""Ellis County, Texas source-safety guard.

Ellis County's former P2C endpoint is stale. The county-linked public LL Hosting
site returned an empty client shell and then an HTTP 403 challenge during normal
aggregate validation. Its client code references search actions, but no supported
broad roster contract was established without submitting search criteria.

The registered path therefore fails closed until an official public roster exposes
complete identity, a source-issued booking or inmate identifier, and a booking date
or timestamp through a supported non-challenged bulk route.
"""
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper


class EllisScraper(BaseScraper):
    """Fail closed while Ellis's public roster contract is unverified."""

    OFFICIAL_INMATE_SEARCH_URL = "https://ecso.llhostings.com/"

    @property
    def county(self) -> str:
        return "Ellis"

    @property
    def state(self) -> str:
        return "TX"

    @property
    def roster_url(self) -> str:
        return self.OFFICIAL_INMATE_SEARCH_URL

    def scrape(self) -> List[ArrestRecord]:
        self.logger.warning(
            "%s %s official roster has no verified non-challenged broad-list contract; no records emitted pending a supported source-safe roster",
            self.county,
            self.state,
        )
        return []
