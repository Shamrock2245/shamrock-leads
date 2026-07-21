"""
Jefferson County (AL) Arrest Scraper — Birmingham metro.

Portal: http://sheriff.jccal.org/NewWorld.InmateInquiry/AL0010000/
Platform: New World InmateInquiry (Tyler Technologies)

Uses APE StealthSession (curl_cffi + Warren/S5W2C/Stormsia) via NewWorldBaseScraper.
Recon 2026-07-20: datacenter IPs return HTTP 403 — residential path required.
"""
from __future__ import annotations

from scrapers.new_world_base import NewWorldBaseScraper


class JeffersonScraper(NewWorldBaseScraper):
    """Jefferson County, AL — New World InmateInquiry (APE-aware)."""

    portal_url = "http://sheriff.jccal.org/NewWorld.InmateInquiry/AL0010000/"

    @property
    def county(self) -> str:
        return "Jefferson"

    @property
    def state(self) -> str:
        return "AL"

    @property
    def base_url(self) -> str:
        return self.portal_url
