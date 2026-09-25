"""
Tennessee TnCIS statewide case inquiry scraper.

Portal: https://lgc-tn.com/tncis-web-inquiry/
Platform: LGC / TnCIS Web Inquiry (Cloudflare protected)

FAIL CLOSED (owner decision 2026-09-25). Inherits the guarded adapter in
``scrapers/counties/tennessee_tncis_v2_ape.py`` (``SOURCE_CONTRACT_VALIDATED =
False``) with multi-state identity (``scraper_tn_tncis`` / State=TN). A
Cloudflare / anti-bot answer raises ``AntiBotBlocked`` (error class
``anti_bot``); there is no Obscura, proxy, or stealth-browser fallback.
"""
from __future__ import annotations

from scrapers.counties.tennessee_tncis_v2_ape import TennesseeTnCISScraperV2APE


class TnCISScraper(TennesseeTnCISScraperV2APE):
    """Statewide TN criminal case inquiry (multi-county)."""

    @property
    def county(self) -> str:
        return "TnCIS"

    @property
    def state(self) -> str:
        return "TN"
