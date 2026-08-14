"""Durham County, North Carolina source-safety guard.

The prior scraper targeted a stale legacy ASP.NET endpoint, disabled TLS verification,
submitted broad A–Z searches, assumed in-custody status, and emitted records without
a source-issued booking-date boundary. That behavior is unsafe for Shamrock's
immutable state/county/booking identity rule.

The registered path therefore fails closed until Durham publishes a supported public
bulk source with complete identity, source-issued booking identifier, and booking
date or timestamp.
"""
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper


class DurhamScraper(BaseScraper):
    """Fail closed while Durham's public booking contract is unverified."""

    OFFICIAL_INFO_URL = "https://www.durhamsheriff.com/community/public-information/inmate-population-search"

    @property
    def county(self) -> str:
        return "Durham"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def roster_url(self) -> str:
        return self.OFFICIAL_INFO_URL

    def scrape(self) -> List[ArrestRecord]:
        self.logger.warning(
            "%s %s source contract is unverified; no records emitted pending a supported complete-identity public roster",
            self.county,
            self.state,
        )
        return []
