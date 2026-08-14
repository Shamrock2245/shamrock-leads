"""Gwinnett County, Georgia source-safety guard.

The official SmartWEB public last-24-hours view exposes source booking numbers and
booking timestamps, but abbreviates given names to initials. The prior scraper
submitted an empty search form and emitted records without a source booking number
or complete identity. That is unsafe for Shamrock's immutable state/county/booking
identity boundary.

This existing scheduled path therefore fails closed until Gwinnett publishes a
supported broad public view with complete identity and source booking fields.
"""
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper


class GwinnettScraper(BaseScraper):
    """Fail closed while Gwinnett's bulk public identity contract is incomplete."""

    OFFICIAL_SOURCE_URL = "https://www.gwinnettcountysheriff.com/smartwebclient/"

    @property
    def county(self) -> str:
        return "Gwinnett"

    @property
    def state(self) -> str:
        return "GA"

    @property
    def roster_url(self) -> str:
        return self.OFFICIAL_SOURCE_URL

    def scrape(self) -> List[ArrestRecord]:
        self.logger.warning(
            "%s %s public SmartWEB bulk view lacks complete names; no records emitted pending a supported identity-safe source contract",
            self.county,
            self.state,
        )
        return []
