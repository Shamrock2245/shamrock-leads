"""
Tennessee TnCIS statewide case inquiry scraper.

Portal: https://lgc-tn.com/tncis-web-inquiry/
Platform: LGC / TnCIS Web Inquiry (Cloudflare protected)

Reuses the existing APE-integrated implementation under
``scrapers/counties/tennessee_tncis_v2_ape.py`` with multi-state identity
(``scraper_tn_tncis`` / State=TN).
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
