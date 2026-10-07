"""
Santa Rosa County (FL) Arrest Scraper — SmartCOP SmartWEB JAIL View.

Source contract (recon 2026-10-07, docs/recon/FL_SMARTWEB_FIVE_2026-10-07.md):
  * URL: https://jailview.srso.net/SmartWebClient/jail.aspx
  * Plain HTTPS ASP.NET WebForms; no login; ordinary access from box.
  * Broad criterion: Begin/End Booking Date + Current Inmates Only.
  * Source-issued Booking No pattern: SRSO<YY>JBN<NNNNNN>.
  * AddMoreResults may 500 on some builds — first-page cards still emit.
  * No invented booking keys; curl_cffi path retired.
"""
from __future__ import annotations

from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from scrapers.fl_smartweb import scrape_smartweb_jail_view
from core.models import ArrestRecord

BASE_URL = "https://jailview.srso.net/SmartWebClient"
FACILITY = "Santa Rosa County Jail"


class SantaRosaCountyScraper(BaseScraper):
    """Santa Rosa County (FL) — SmartWEB JAIL View (Milton)."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Santa Rosa"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self, lookback_days: Optional[int] = None) -> List[ArrestRecord]:
        return scrape_smartweb_jail_view(
            county=self.county,
            facility=FACILITY,
            base_url=BASE_URL,
            lookback_days=lookback_days,
            log_prefix="Santa Rosa",
        )
