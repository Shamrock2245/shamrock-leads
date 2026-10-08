"""
Glades County (FL) Arrest Scraper — SmartCOP SmartWEB JAIL View.

Source contract (2026-10-08, docs/recon/FL_HOME_COUNTIES_SOURCE_CONTRACT_2026-10-08.md
and docs/recon/FL_GLADES_BOND_ZERO_2026-10-08.md):
  * URL: https://smartweb.gladessheriff.org/smartwebclient/Jail.aspx
  * Plain HTTPS ASP.NET WebForms (Cloudflare-fronted, no challenge); no login.
  * Booking-date window + Current Inmates Only, then the legacy AddMoreResults
    page method, all via the shared ``scrapers/fl_smartweb.py`` helper.
  * Source-issued Booking No pattern: GCSO<YY>JBN<NNNNNN>.

2026-10-08: curl_cffi Chrome impersonation is retired. The site answers plain
``requests`` (live: HTTP 200, no challenge, 28/28 current cards keyed on the
source booking number). Bond rules are the shared helper's, which match the
CoS decision for Glades: a printed charge ``$0.00`` is a real 0; a card-level
``$0.00`` is the JAIL View default and stays unknown (""); any NO BOND or
unreadable charge cell makes the booking total unknown; a run with no charges
anywhere raises instead of writing blank charges.
"""
from __future__ import annotations

from typing import List, Optional

from core.models import ArrestRecord
from scrapers import fl_smartweb
from scrapers.base_scraper import BaseScraper
from scrapers.fl_smartweb import scrape_smartweb_jail_view

BASE_URL = "https://smartweb.gladessheriff.org/smartwebclient"
FACILITY = "Glades County Jail"
# Small jail; a one-year booking window covers every current inmate (live
# 2026-10-08: 28 current cards over 365 days).
LOOKBACK_DAYS = 365


class GladesCountyScraper(BaseScraper):
    """Glades County (FL) — SmartWEB JAIL View (Moore Haven), plain requests."""

    @property
    def county(self) -> str:
        return "Glades"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self, lookback_days: Optional[int] = None) -> List[ArrestRecord]:
        return scrape_smartweb_jail_view(
            county=self.county,
            facility=FACILITY,
            base_url=BASE_URL,
            lookback_days=LOOKBACK_DAYS if lookback_days is None else lookback_days,
            log_prefix="Glades",
        )

    def _parse_html(self, html: str, seen: set) -> List[ArrestRecord]:
        """Parse JAIL View card HTML with the shared helper (kept for tests)."""
        return fl_smartweb._parse_html(
            html, seen, county=self.county, facility=FACILITY, detail_url=f"{BASE_URL}/jail.aspx"
        )
