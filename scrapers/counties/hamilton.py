"""
Hamilton County (FL) Arrest Scraper — SmartCOP SmartWEB JAIL View.

Source contract (recon 2026-10-07, docs/recon/FL_IDLE_EIGHT_2026-10-07.md):
  * URL: https://inmate.hamiltonsheriff.com/smartwebclient/jail.aspx
  * Plain HTTPS ASP.NET WebForms; no login; ordinary public access from box.
  * Broad criterion: Begin/End Booking Date + Current Inmates Only.
  * Source-issued Booking No pattern: HCSO<YY>JBN<NNNNNN>; charges + bond on card.
  * Prior host smartcop.hamiltoncountysheriff.com is NXDOMAIN — do not restore it.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from scrapers.fl_smartweb import scrape_smartweb_jail_view
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://inmate.hamiltonsheriff.com/smartwebclient"
FACILITY = "Hamilton County Jail"


class HamiltonCountyScraper(BaseScraper):
    """Hamilton County (FL) — SmartWEB JAIL View (Jasper)."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Hamilton"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self, lookback_days: Optional[int] = None) -> List[ArrestRecord]:
        return scrape_smartweb_jail_view(
            county=self.county,
            facility=FACILITY,
            base_url=BASE_URL,
            lookback_days=lookback_days,
            log_prefix="Hamilton",
        )
