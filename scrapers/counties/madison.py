"""
Madison County (FL) Arrest Scraper — SmartCOP SmartWEB JAIL View.

Source contract (recon 2026-10-07, docs/recon/FL_IDLE_EIGHT_2026-10-07.md):
  * URL: https://smartweb.mcso-fl.org/smartwebclient/jail.aspx
  * Plain HTTPS ASP.NET WebForms; no login; ordinary public access from box.
  * Broad criterion: Begin/End Booking Date + Current Inmates Only.
  * Source-issued Booking No pattern: MCSO<YY>JBN<NNNNNN>; charges + bond on card.
  * Prior host smartcop.madisonsheriff.org is the wrong tenant (expired cert /
    not Madison FL) — do not restore it. Official SO site is madisonflsheriff.org.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from scrapers.fl_smartweb import scrape_smartweb_jail_view
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://smartweb.mcso-fl.org/smartwebclient"
FACILITY = "Madison County Jail"


class MadisonCountyScraper(BaseScraper):
    """Madison County (FL) — SmartWEB JAIL View (Madison)."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Madison"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self, lookback_days: Optional[int] = None) -> List[ArrestRecord]:
        return scrape_smartweb_jail_view(
            county=self.county,
            facility=FACILITY,
            base_url=BASE_URL,
            lookback_days=lookback_days,
            log_prefix="Madison",
        )
