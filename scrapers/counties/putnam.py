"""
Putnam County (FL) Arrest Scraper — SmartCOP SmartWEB JAIL View.

Source contract (recon 2026-10-07, docs/recon/FL_SMARTWEB_LEGACY_PAGING_2026-10-07.md):
  * URL: https://smartweb.pcso.us/smartwebclient/Jail.aspx (linked from pcso.us)
  * Plain HTTPS ASP.NET WebForms; no login, no CAPTCHA.
  * Broad criterion: Begin/End Booking Date + Current Inmates Only.
  * Legacy ``AddMoreResults`` page method (bare SearchVals, ``d.Data`` reply).
  * Source-issued Booking No pattern: PCSO<YY>JBN<NNNNNN>.
  * Cards without a matching image ``bookno`` + ``Booking No:`` text are dropped.
  * No invented booking keys. Health stays unverified until a write smoke.
"""
from __future__ import annotations

from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from scrapers.fl_smartweb import scrape_smartweb_jail_view
from core.models import ArrestRecord

BASE_URL = "https://smartweb.pcso.us/smartwebclient"
SEARCH_URL = f"{BASE_URL}/Jail.aspx"
FACILITY = "Putnam County Jail"


class PutnamCountyScraper(BaseScraper):
    """Putnam County (FL) — SmartWEB JAIL View."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Putnam"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self, lookback_days: Optional[int] = None) -> List[ArrestRecord]:
        return scrape_smartweb_jail_view(
            county=self.county,
            facility=FACILITY,
            base_url=BASE_URL,
            lookback_days=lookback_days,
            log_prefix="Putnam",
        )
