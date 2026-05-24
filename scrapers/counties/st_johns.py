"""
St. Johns County Arrest Scraper — STUB (No Public Roster)
Source: St. Johns County Sheriff's Office
URL: https://www.sjso.org/sj-inmate-search/
Status: NO PUBLIC ONLINE ROSTER — taken down due to security concerns.

NOTE: St. Johns County took down their online inmate search portal.
This scraper returns an empty list. When a public API or web roster becomes available,
implement it here following the standard BaseScraper pattern.
"""

import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

FACILITY = "St. Johns County Jail"
INFO_URL = "https://www.sjso.org/sj-inmate-search/"


class StJohnsCountyScraper(BaseScraper):
    """St. Johns County (FL) — No public roster. Stub scraper."""

    @property
    def county(self) -> str:
        return "St. Johns"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning(
            "St. Johns County: No public online web roster available (taken down). "
            "Implement this scraper when a public web API/roster is discovered."
        )
        return []

