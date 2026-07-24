"""
Santa Rosa County Arrest Scraper — SmartCop AJAX (AddMoreResults)
Source: Santa Rosa County Sheriff's Office
URL: https://jailview.srso.net/SmartWebClient/jail.aspx
Method: requests + BeautifulSoup — Wildcard (%) search + direct AJAX AddMoreResults loop.
"""

import json
import logging
import re
import time
from typing import List
from datetime import datetime, timezone

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

from curl_cffi import requests as cffi_requests
logger = logging.getLogger(__name__)

BASE_URL = "https://jailview.srso.net/SmartWebClient"
SEARCH_URL = f"{BASE_URL}/jail.aspx"
AJAX_URL = f"{BASE_URL}/jail.aspx/AddMoreResults"
FACILITY = "Santa Rosa County Jail"
PAGE_SIZE = 185  # SmartCop default batch size

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": SEARCH_URL,
}

class SantaRosaCountyScraper(BaseScraper):
    """Santa Rosa County (FL) — SmartCop AJAX jail roster (Milton/Pensacola area)"""

    @property
    def county(self) -> str:
        return "Santa Rosa"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed")
            raise

        session = cffi_requests.Session()
        session.headers.update(HEADERS)

        # Step 1: Initial GET request to retrieve standard ASP.NET ViewState tokens
        try:
            logger.info(f"Santa Rosa: Loading initial page from {SEARCH_URL}")
            resp = session.get(SEARCH_URL, timeout=30, verify=False, impersonate=IMPERSONATE)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Santa Rosa: Initial GET failed: {e}")
            raise

        soup = BeautifulSoup(resp.text, "html.parser")

        def _get_hidden(name):
            el = soup.find("input", {"name": name}) or soup.find("input", {"id": name})
            return el["value"] if el and el.get("value") else ""

        viewstate = _get_hidden("__VIEWSTATE")
        viewstate_generator = _get_hidden("__VIEWSTATEGENERATOR")
        event_validation = _get_hidden("__EVENTVALIDATION")

        seen_bookings = set()
        all_records = []

        # Step 2: Search for '%' in LastName to bypass empty validation and match all
        logger.info("Santa Rosa: Initiating wildcard (%) search POST...")
        post_data = {
            "__VIEWSTATE": viewstate,
            "__VIEWSTATEGENERATOR": viewstate_generator,
            "__EVENTVALIDATION": event_validation,
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "txbLastName": "%",
            "txbFirstName": "",
            "tbDateOfBirth": "",
            "TypeSearch": "0",  # Current Inmates Only
            "SearchSortOption": "1", # Sorted by BookingDate
            "SearchOrderOption": "1", # Descending
            "btnSumit": "Submit",
        }

        try:
            resp2 = session.post(SEARCH_URL, data=post_data, timeout=30, verify=False, impersonate=IMPERSONATE)
            resp2.raise_for_status()
        except Exception as e:
            logger.error(f"Santa Rosa: Wildcard POST search failed: {e}")
            raise

        initial_records = self._parse_html(resp2.text, seen_bookings)
        all_records.extend(initial_records)
        logger.info(f"Santa Rosa: Initial search returned {len(initial_records)} records.")

        # Step 3: Loop calling jail.aspx/AddMoreResults to get subsequent records
        records_loaded = len(initial_records)
        json_headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": SEARCH_URL
        }

        max_pages = 50  # Safety limit
        page_idx = 1

        while page_idx <= max_pages:
            logger.info(f"Santa Rosa: Fetching page {page_idx+1} (loaded so far: {records_loaded})...")
            payload = {
                "FirstName": "",
                "MiddleName": "",
                "LastName": "%",
                "BeginBookDate": "",
                "EndBookDate": "",
                "BeginReleaseDate": "",
                "EndReleaseDate": "",
                "TypeJailSearch": 0,
                "RecordsLoaded": records_loaded,
                "SortOption": 1,
                "SortOrder": 1,
                "IsDefault": False,
            }

            try:
                resp3 = session.post(AJAX_URL, json=payload, headers=json_headers, timeout=30, verify=False, impersonate=IMPERSONATE)
                resp3.raise_for_status()

                res_data = resp3.json().get("d", {})
                if isinstance(res_data, dict):
                    res_data = res_data.get("Data", res_data)
                
                results_returned = res_data.get("resultsReturned", 0) if isinstance(res_data, dict) else 0
                html_snippet = res_data.get("data", "") if isinstance(res_data, dict) else ""

                if results_returned == 0 or not html_snippet:
                    logger.info("Santa Rosa: AJAX returned 0 records. Roster fully loaded.")
                    break

                more_records = self._parse_html(html_snippet, seen_bookings)
                all_records.extend(more_records)
                logger.info(f"Santa Rosa: Page {page_idx+1} loaded {len(more_records)} records.")
                
                records_loaded += results_returned

                results_attempted = res_data.get("resultsAttempted", 0) if isinstance(res_data, dict) else 0
                if results_attempted > results_returned:
                    logger.info("Santa Rosa: Reached end of results (attempted > returned).")
                    break

                page_idx += 1
                time.sleep(0.5)

            except Exception as e:
                logger.warning(f"Santa Rosa: AJAX page {page_idx+1} load failed: {e}")
                break

        logger.info(f"Santa Rosa County Scrape Complete: {len(all_records)} total records")
        return all_records

    def _parse_html(self, html: str, seen: set) -> List[ArrestRecord]:
        """Delegate to shared SmartWeb card parser (ENLARGE PHOTO safe)."""
        from scrapers.smartweb_card_parser import parse_smartweb_cards

        return parse_smartweb_cards(
            html,
            county=self.county,
            facility=FACILITY,
            detail_url=SEARCH_URL,
            seen=seen,
            state="FL",
            log_prefix="Santa Rosa",
        )
