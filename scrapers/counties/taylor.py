"""
Taylor County (FL) Arrest Scraper — SmartCOP SmartWEB JAIL View.

Source contract (recon 2026-10-07, docs/recon/FL_SMARTWEB_FIVE_2026-10-07.md):
  * URL: http://smartcop.taylorsheriff.org:8989/SmartWEBClient/Jail.aspx
  * Plain HTTP ASP.NET WebForms on SO-published port 8989; no login.
  * Broad criterion: Begin/End Booking Date (this build omits TypeSearch).
  * Source-issued Booking No pattern: TCSO<YY>JBN<NNNNNN>.
  * No invented booking keys.
"""
from __future__ import annotations

from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from scrapers.fl_smartweb import scrape_smartweb_jail_view
from core.models import ArrestRecord

BASE_URL = "http://smartcop.taylorsheriff.org:8989/SmartWEBClient"
FACILITY = "Taylor County Jail"


class TaylorCountyScraper(BaseScraper):
    """Taylor County (FL) — SmartWEB JAIL View."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Taylor"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self, lookback_days: Optional[int] = None) -> List[ArrestRecord]:
        return scrape_smartweb_jail_view(
            county=self.county,
            facility=FACILITY,
            base_url=BASE_URL,
            lookback_days=lookback_days,
            log_prefix="Taylor",
        )
