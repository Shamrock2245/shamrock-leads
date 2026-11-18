"""Sarasota County, Florida scraper.

The detail-page parser lives in ``sarasota_contract.py``. This class stays
fail-closed: ``scrape()`` returns nothing and does not fetch. A later
promotion would call ``build_records``, which caps detail fetches the same
way Hernando does.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Tuple

from core.models import ArrestRecord
from scrapers.access_strategy import SARASOTA_JAIL
from scrapers.counties.sarasota_contract import (
    MAX_DETAILS_PER_RUN,
    REQUEST_PAUSE_S,
    walk_detail_pages,
)
from scrapers.jailtracker_base import JailTrackerBaseScraper

logger = logging.getLogger(__name__)


class SarasotaCountyScraper(JailTrackerBaseScraper):
    """Fail closed. The detail parser is ready; this flag still blocks fetch.

    Owner directive 2026-11-13 keeps the fetch plan. The 2026-11-18 recon
    found a 12-digit Booking Number on the detail page. The listing still has
    no booking number. ``ACCESS_PLAN`` is unchanged.
    """

    ACCESS_PLAN = SARASOTA_JAIL
    county_jt_id = "SARASOTA_COUNTY_FL"
    facility_name = "Sarasota County Jail"
    DETAIL_CAP = MAX_DETAILS_PER_RUN
    DETAIL_PAUSE_S = REQUEST_PAUSE_S
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "official Sarasota listing has no booking number (person link id only). "
        "2026-11-18 recon: the detail page charge table publishes a 12-digit "
        "Booking Number and an Intake Date/Time. That parser is in "
        "sarasota_contract.py, but this flag stays false, so scrape emits nothing "
        "and does not fetch."
    )

    @property
    def county(self) -> str:
        return "Sarasota"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Sarasota FL fails closed: %s", self.SOURCE_CONTRACT_REASON
        )
        return []

    def build_records(
        self,
        listing_html: str,
        fetch_detail: Callable[[str], str],
        *,
        sleep: Callable[[float], None],
        limit: int | None = None,
        pause_s: float | None = None,
    ) -> Tuple[List[ArrestRecord], Dict[str, int]]:
        """Detail walk a promotion would run. ``scrape()`` does not call this."""
        return walk_detail_pages(
            listing_html,
            fetch_detail,
            sleep=sleep,
            limit=self.DETAIL_CAP if limit is None else limit,
            pause_s=self.DETAIL_PAUSE_S if pause_s is None else pause_s,
        )
