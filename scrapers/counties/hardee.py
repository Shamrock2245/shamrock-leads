"""
Hardee County Arrest Scraper — STUB (No Public Roster)
Source: Hardee County Sheriff's Office
URL: https://apps.myocv.com/share/a27833873
Status: NO PUBLIC ONLINE ROSTER — Mobile App only.

NOTE: Hardee County uses OCV Mobile App which decommissioned their web-based search.
This scraper returns an empty list. When a public API or web roster becomes available,
implement it here following the standard BaseScraper pattern.
"""

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

FACILITY = "Hardee County Jail"
INFO_URL = "https://apps.myocv.com/share/a27833873"


class HardeeCountyScraper(BaseScraper):
    """Hardee County (FL) — No public web roster (Mobile App only). Stub scraper."""

    @property
    def county(self) -> str:
        return "Hardee"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Hardee County: No public online web roster available (Mobile App only). "
            "Implement this scraper when a public web API/roster is discovered."
        )
        return []

