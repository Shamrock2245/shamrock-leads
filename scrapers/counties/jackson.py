"""
Jackson County Arrest Scraper — STUB (No Public Roster)
Source: Jackson County Correctional Facility
URL: https://jacksoncountyfl.gov/services/correctional-facility/
Status: NO PUBLIC ONLINE ROSTER — phone inquiries only: (850) 482-9651

NOTE: Jackson County does not publish a public inmate search or jail roster online.
This scraper returns an empty list. When a public API or roster becomes available,
implement it here following the standard BaseScraper pattern.

To activate: Discover the roster URL and implement scrape() accordingly.
"""

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

FACILITY = "Jackson County Correctional Facility"
INFO_URL = "https://jacksoncountyfl.gov/services/correctional-facility/"


class JacksonCountyScraper(BaseScraper):
    """Jackson County (FL) — No public roster. Stub scraper."""

    @property
    def county(self) -> str:
        return "Jackson"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "Jackson County: No public online roster available. "
            "Contact (850) 482-9651 for inmate information. "
            "Implement this scraper when a public API/roster is discovered."
        )
        return []
