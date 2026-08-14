"""Connecticut Department of Correction source-safety guard.

The previous statewide path disabled TLS verification, performed a broad A–Z search
walk, retained date of birth, and emitted records when a source-issued booking or
admission date was unavailable. The official CT DOC public search is currently
access-rejected by its BITS BOT control, so the live field contract cannot be
validated without bypassing access controls.

This registered statewide path therefore fails closed until Connecticut publishes a
supported public bulk source with complete identity, source-issued inmate or booking
identifier, and a booking or admission date.
"""
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper


class CTDOCInmateScraper(BaseScraper):
    """Fail closed while the statewide CT DOC public source is access-rejected."""

    OFFICIAL_SEARCH_URL = "https://www.ctinmateinfo.state.ct.us/searchop.asp"

    @property
    def county(self) -> str:
        return "CT DOC"

    @property
    def state(self) -> str:
        return "CT"

    @property
    def scraper_id(self) -> str:
        return "scraper_ct_doc"

    @property
    def roster_url(self) -> str:
        return self.OFFICIAL_SEARCH_URL

    def scrape(self) -> List[ArrestRecord]:
        self.logger.warning(
            "%s %s public source is access-rejected and has no verified booking-safe bulk contract; no records emitted",
            self.county,
            self.state,
        )
        return []
