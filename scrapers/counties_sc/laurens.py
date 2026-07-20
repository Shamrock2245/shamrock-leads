"""
Laurens County (SC) Arrest Scraper — Zuercher portal.

Recon 2026-07-20: the portal serves the Angular shell, but the inmates
module endpoints (/api/portal/inmates/init and /load) return 404 — the
county has the roster module disabled server-side. The base scraper
degrades gracefully (empty list + warning). Re-probe periodically; if
Laurens re-enables the module, this wrapper works with zero changes.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class LaurensScraper(ZuercherBaseScraper):
    @property
    def county(self) -> str:
        return "Laurens"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def zuercher_domain(self) -> str:
        return "laurens-911-sc.zuercherportal.com"
