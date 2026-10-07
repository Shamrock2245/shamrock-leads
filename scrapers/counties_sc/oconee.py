"""
Oconee County (SC) Arrest Scraper — Zuercher portal.

Recon 2026-10-07: public inmates roster exists, but listing/detail rows do not
expose a source-issued booking or inmate ID. Hold fail_closed — do not invent
keys. See docs/recon/SC_OCONEE_PICKENS_ZUERCHER_2026-10-07.md.
"""
from scrapers.zuercher_base import ZuercherBaseScraper


class OconeeScraper(ZuercherBaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_SAFETY_REASON = 'official Zuercher roster lacks a source-issued booking or inmate ID and booking timestamp'

    @property
    def county(self) -> str:
        return "Oconee"

    @property
    def state(self) -> str:
        return "SC"

    @property
    def zuercher_domain(self) -> str:
        return "oconee-so-sc.zuercherportal.com"
