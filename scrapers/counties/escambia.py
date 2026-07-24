"""
Escambia County Arrest Scraper — SmartCOP SmartWeb.
Source: Escambia County Sheriff's Office
URL: https://inmatelookup.myescambia.com/smartwebclient/jail.aspx
Method: curl_cffi (chrome131 impersonation) + shared smartweb_parser
Stealth: Direct-first (proxy CONNECT 502s common on SmartWEB hosts)
"""
import logging
import urllib3
from typing import List

from curl_cffi import requests as cffi_requests
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord
from scrapers.smartweb_parser import scrape_smartweb

logger = logging.getLogger(__name__)

BASE_URL = "https://inmatelookup.myescambia.com"
SEARCH_URL = f"{BASE_URL}/smartwebclient/jail.aspx"
FACILITY = "Escambia County Jail"

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


class EscambiaCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Escambia"

    def scrape(self) -> List[ArrestRecord]:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        session = cffi_requests.Session(impersonate=IMPERSONATE)

        try:
            logger.info(f"Starting Escambia SmartWeb scrape using base URL: {SEARCH_URL}")
            records = scrape_smartweb(
                base_url=SEARCH_URL,
                county=self.county,
                facility=FACILITY,
                session=session,
                ArrestRecord=ArrestRecord
            )
            logger.info(f"Escambia: {len(records)} records scraped successfully")
            return records
        except Exception as e:
            logger.error(f"Escambia SmartWeb fatal: {e}")
            raise
