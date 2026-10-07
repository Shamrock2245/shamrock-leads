"""
Escambia County (FL) Arrest Scraper — SmartCOP SmartWEB JAIL View.

Source contract (recon 2026-10-07, docs/recon/FL_SMARTWEB_FIVE_2026-10-07.md):
  * URL: https://inmatelookup.myescambia.com/smartwebclient/jail.aspx
  * Plain HTTPS ASP.NET WebForms; no login; ordinary access from box.
  * Broad criterion: Begin/End Booking Date + Current Inmates Only.
  * Source-issued Booking No pattern: ECC<YY>JBN<NNNNNN>.
  * No Odyssey / invented keys; curl_cffi path retired.
"""
from __future__ import annotations

from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from scrapers.fl_smartweb import scrape_smartweb_jail_view
from core.models import ArrestRecord

BASE_URL = "https://inmatelookup.myescambia.com/smartwebclient"
FACILITY = "Escambia County Jail"


class EscambiaCountyScraper(BaseScraper):
    """Escambia County (FL) — SmartWEB JAIL View (Pensacola)."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Escambia"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self, lookback_days: Optional[int] = None) -> List[ArrestRecord]:
        return scrape_smartweb_jail_view(
            county=self.county,
            facility=FACILITY,
            base_url=BASE_URL,
            lookback_days=lookback_days,
            log_prefix="Escambia",
        )
