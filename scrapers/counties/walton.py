"""
Walton County (FL) Arrest Scraper — New World InmateInquiry.

Source contract (recon 2026-10-07, docs/recon/FL_BREVARD_NEWWORLD_2026-10-07.md):
  * Portal: https://nwscorrections.waltonso.org/NewWorld.InMateInquiry/WaltonCounty/
  * Ordinary public HTTPS; ``InCustody=True`` roster, ``Page=N`` paging; plain
    ``requests`` with TLS verification on (curl_cffi impersonation retired).
  * Booking_Number = source ``Booking YYYY-NNNNNNNN`` of the open booking on the
    detail page. The old parser keyed every row on the "Booking History"
    heading (one shared key ``History``) and walked ``InCustody=False``.
  * Bond = source Total Bond Amount of that booking only. Health stays
    unverified until a write smoke.
"""
from __future__ import annotations

from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper
from scrapers.fl_newworld import scrape_newworld_roster

PORTAL_URL = "https://nwscorrections.waltonso.org/NewWorld.InMateInquiry/WaltonCounty/"
FACILITY = "Walton County Jail"


class WaltonCountyScraper(BaseScraper):
    """Walton County (FL) — New World InmateInquiry (DeFuniak Springs)."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Walton"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        return scrape_newworld_roster(county=self.county, facility=FACILITY, portal_url=PORTAL_URL)
