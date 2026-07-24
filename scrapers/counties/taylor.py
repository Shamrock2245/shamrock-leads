"""
Taylor County Arrest Scraper — SmartCOP ASP.NET.
Source: Taylor County Sheriff's Office
Public entry: http://jail.taylorsheriff.org/ → redirects to
  http://smartcop.taylorsheriff.org:8989/SmartWEBClient/Jail.aspx
(HTTPS :443 is dead; use HTTP on port 8989.)
"""
import logging
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord
from scrapers.smartweb_parser import scrape_smartweb

logger = logging.getLogger(__name__)

SEARCH_URL = "http://smartcop.taylorsheriff.org:8989/SmartWEBClient/Jail.aspx"
FACILITY = "Taylor County Jail"

# ── Stealth Stack ──────────────────────────────────────────────────────────────
IMPERSONATE = "chrome131"
STEALTH_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
}

class TaylorCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Taylor"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except ImportError:
            logger.error("requests not installed")
            raise

        session = cffi_requests.Session()
        # Direct only — CONNECT proxies rarely support custom ports like :8989
        try:
            records = scrape_smartweb(
                base_url=SEARCH_URL,
                county=self.county,
                facility=FACILITY,
                session=session,
                ArrestRecord=ArrestRecord,
            )
            # Fallback via public entry host if direct port fails
            if not records:
                entry = "http://jail.taylorsheriff.org/"
                r = session.get(entry, timeout=20, allow_redirects=True, impersonate=IMPERSONATE)
                final = r.url or SEARCH_URL
                records = scrape_smartweb(
                    base_url=final,
                    county=self.county,
                    facility=FACILITY,
                    session=session,
                    ArrestRecord=ArrestRecord,
                )
            logger.info(f"Taylor: {len(records)} records")
            return records
        except Exception as e:
            logger.error(f"Taylor: scrape failed: {e}")
            raise
