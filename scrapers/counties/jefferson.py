"""
Jefferson County (FL) Arrest Scraper — STUB (No Public Roster)
Source: Jefferson County Sheriff's Office
Status: NO PUBLIC ONLINE ROSTER — county does not publish inmate data online.
NOTE: Jefferson County is one of Florida's smallest counties and does not maintain
a public inmate search or jail roster website. This scraper returns an empty list.
When a public API or roster becomes available, implement it here following the
standard BaseScraper pattern with APE stealth session.
"""
import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

FACILITY = "Jefferson County Jail"


class JeffersonCountyScraper(BaseScraper):
    """Jefferson County (FL) — No public roster. Stub scraper."""

    @property
    def county(self) -> str:
        return "Jefferson"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        logger.info(f"Jefferson: No public roster available — returning empty")
        return []
