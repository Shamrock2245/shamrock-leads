"""
Bradford County (FL) Arrest Scraper — SmartCOP SmartWEB JAIL View.

Source contract (recon 2026-10-07, docs/recon/FL_SMARTWEB_FIVE_2026-10-07.md):
  * URL: http://smartweb.bradfordsheriff.org/smartwebclient/Jail.aspx (HTTP only)
  * Plain public ASP.NET WebForms; no login; ordinary access from box.
  * Broad criterion: Begin/End Booking Date (+ TypeSearch Current Inmates when present).
  * Source-issued Booking No pattern: BCSO<YY>JBN<NNNNNN>; charges + bond on card.
  * Prior host smartcop.bradfordsheriff.org is NXDOMAIN — do not restore it.
  * No invented name/date booking keys.
"""
from __future__ import annotations

from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from scrapers.fl_smartweb import scrape_smartweb_jail_view
from core.models import ArrestRecord

BASE_URL = "http://smartweb.bradfordsheriff.org/smartwebclient"
FACILITY = "Bradford County Jail"


class BradfordCountyScraper(BaseScraper):
    """Bradford County (FL) — SmartWEB JAIL View."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Bradford"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self, lookback_days: Optional[int] = None) -> List[ArrestRecord]:
        return scrape_smartweb_jail_view(
            county=self.county,
            facility=FACILITY,
            base_url=BASE_URL,
            lookback_days=lookback_days,
            log_prefix="Bradford",
        )
