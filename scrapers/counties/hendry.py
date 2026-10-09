"""
Hendry County (FL) — FAIL CLOSED (2026-10-08)
=============================================
Source checked: Hendry County Sheriff's Office via MyOCV CMS,
``https://myocv.s3.amazonaws.com/ocvapps/a102933935/inmates.json``.

The feed is public (HTTP 200, ~288 rows), but its only identifier is
``inmateID`` = ``HCSO<YY>MNI<NNNNNN>``. MNI is a Master Name Index (person)
id, not a booking number: its two-digit year runs from 00 to 26 whatever the
Booked Date year is, so the same person keeps the same id across bookings.
``chargeArray`` is schema-only (field names, no charge rows) and no bond is
published. Under the person-id rule (Alachua #107, Clay FL, Durham NC) the
county is fail closed until the source publishes a booking number. CoS
approved 2026-10-08. See ``docs/recon/FL_HENDRY_FAIL_CLOSED_2026-10-08.md``.

Rows already stored under MNI keys are not touched here; any cleanup waits on
Brendan's OK.

HISTORY:
  - v1: OCV SPA HTML enrichment (unreliable)
  - v2: JailTracker Blazor WASM + CAPTCHA (broken)
  - v3: BustedNewspaper RSS (blocked/aborted from VPS 2026-07)
  - v4: OCV S3 JSON feed, keyed on the MNI person id (retired 2026-10-08)
"""

from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

OCV_INMATES_URL = "https://myocv.s3.amazonaws.com/ocvapps/a102933935/inmates.json"
COUNTY = "Hendry"


class HendryCountyScraper(BaseScraper):
    """Fail closed: the public feed has only a person id (MNI), no booking number."""

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "MyOCV inmates.json identifies rows only by inmateID HCSO<YY>MNI<NNNNNN>, "
        "a person-level Master Name Index id, not a source booking number; "
        "refuse emission rather than key bookings on a person id."
    )

    @property
    def county(self) -> str:
        return COUNTY

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning("Hendry: SOURCE_CONTRACT_VALIDATED=False — %s", self.SOURCE_CONTRACT_REASON)
        return []
