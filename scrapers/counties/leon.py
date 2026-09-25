"""Leon County (FL) — fail closed (Akamai 403 to box egress; no listing booking #).

2026-09-25 recon (docs/recon/FL_GAP_QUEUE_2026-09-25.md): every path on
www.leoncountyso.com, including the official Inmate Search
(/About-us/Departments/Detention-Facility/Inmate-search), returns
``403 Access Denied`` from ``AkamaiGHost`` to plain HTTPS from the datacenter
egress. The page itself is public (it renders from other networks), so this is
an edge/IP block, not a site change. Separately, the public results listing
columns are Photo / Full Name / Last Arrest Date / Last Release Date / In Jail? /
Charge(s) / Arresting Agency — no source booking number on the listing, and it
is a name search.

No WAF bypass, stealth browser or proxy routing is allowed, so the module
refuses every source fetch until a plainly accessible roster with a
source-issued booking identifier is verified.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.leoncountyso.com/About-us/Departments/Detention-Facility/Inmate-search"


class LeonCountyScraper(BaseScraper):
    """Fail closed: Akamai 403 to datacenter egress; listing has no booking number."""

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "leoncountyso.com returns Akamai 403 to datacenter egress (no WAF bypass/proxy); "
        "public name-search listing shows no source booking number."
    )

    @property
    def county(self) -> str:
        return "Leon"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning("Leon FL fails closed: %s", self.SOURCE_CONTRACT_REASON)
        return []
