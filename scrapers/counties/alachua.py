"""
Alachua County (FL) — fail closed (public roster has no source booking ID).

Source recon 2026-10-07 (docs/recon/FL_ALACHUA_FAIL_CLOSED_2026-10-07.md):
  * URL: https://asosite.alachuasheriff.org/ASOInmateLookup.aspx ("View All")
  * Plain HTTPS returns a public ASP.NET GridView (~987 rows) with columns
    Last Name / FirstName / Full Name / Book Date / Race / Sex / Age / POD /
    Arrest Agency. No Booking # column is published; the row link is a
    last/first-name query, and the only identifier behind it is person-level
    (MNI), which is not a booking key.
  * The previous parser read column 2 (FirstName) as Booking_Number, so 984
    rows collapsed onto ~618 first-name keys. Person IDs and names are never
    booking keys (same policy as Durham NC and Clay FL), so emission is
    refused until a source-issued booking number appears on the listing.
  * The form's booking-number search box is not a listing; sequential
    identifier probing stays prohibited.
"""
from __future__ import annotations

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

ROSTER_URL = "https://asosite.alachuasheriff.org/ASOInmateLookup.aspx"


class AlachuaCountyScraper(BaseScraper):
    """Fail closed: public roster has no source booking number."""

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "asosite.alachuasheriff.org View All is a public name/Book Date grid "
        "with no source booking number (only a person-level MNI); refuse "
        "emission rather than key rows on names."
    )

    @property
    def county(self) -> str:
        return "Alachua"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning("Alachua: SOURCE_CONTRACT_VALIDATED=False — %s", self.SOURCE_CONTRACT_REASON)
        return []
