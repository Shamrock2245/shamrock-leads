"""Lake County (FL) — fail closed (Cloudflare Turnstile token required).

2026-09-25 recon (docs/recon/FL_GAP_QUEUE_2026-09-25.md): the official LCSO
Inmate Search (https://www.lcso.org/inmate-search/) is served from the box with
HTTP 200, but its only data endpoint ``POST /inmate-search/api/inmates``
returns HTTP 400 ``{"required": {"token": "(string) reCAPTCHA token from
client-side"}}`` without a Cloudflare Turnstile (reCAPTCHA-compat) token.

The previous implementation bought tokens from a third-party CAPTCHA-solving
service. That is a CAPTCHA bypass and is not allowed, so the module now refuses
every source fetch and emits nothing until LCSO publishes a public roster that
is plainly accessible without a challenge token.
"""
from __future__ import annotations

import logging
from typing import List

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

SEARCH_PAGE_URL = "https://www.lcso.org/inmate-search/"


class LakeCountyScraper(BaseScraper):
    """Fail closed: LCSO inmate API requires a Turnstile/reCAPTCHA token."""

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "LCSO inmate API requires a Cloudflare Turnstile (reCAPTCHA-compat) token; "
        "no CAPTCHA solving or bypass. Hold until a challenge-free public roster exists."
    )

    @property
    def county(self) -> str:
        return "Lake"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        logger.warning("Lake FL fails closed: %s", self.SOURCE_CONTRACT_REASON)
        return []
