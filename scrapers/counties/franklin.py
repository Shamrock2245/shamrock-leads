"""
Franklin County (FL) Arrest Scraper — STUB (No Public Roster)
Source: Franklin County Sheriff's Office
Status: NO PUBLIC ONLINE ROSTER — county does not publish inmate data online.
NOTE: Franklin County is one of Florida's smallest counties and does not maintain
a public inmate search or jail roster website. This scraper returns an empty list.
When a public API or roster becomes available, implement it here following the
standard BaseScraper pattern with APE stealth session.
"""
import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

FACILITY = "Franklin County Jail"


class FranklinCountyScraper(BaseScraper):
    """Franklin County (FL) — No public roster. Stub scraper."""

    @property
    def county(self) -> str:
        return "Franklin"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        logger.info(f"Franklin: No public roster available — returning empty")
        return []
