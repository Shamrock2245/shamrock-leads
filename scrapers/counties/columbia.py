"""
Columbia County Arrest Scraper — Florida SmartCOP SmartWeb.
Source: Columbia County Sheriff's Office (Florida)
URL: http://50.204.15.10/smartwebclient/Jail.aspx
Method: requests + BeautifulSoup — Wildcard (%) search + direct AJAX AddMoreResults loop.
"""
import logging
import re
import time
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

from curl_cffi import requests as cffi_requests
logger = logging.getLogger(__name__)

BASE_URL = "http://50.204.15.10"
SEARCH_URL = f"{BASE_URL}/smartwebclient/Jail.aspx"
ADD_MORE_URL = f"{BASE_URL}/smartwebclient/Jail.aspx/AddMoreResults"
FACILITY = "Columbia County Detention Facility"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": SEARCH_URL,
}

class ColumbiaCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Columbia"

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
            logger.info(f"Columbia: Loading initial page from {SEARCH_URL}")
            resp = session.get(SEARCH_URL, timeout=30, verify=False, impersonate=IMPERSONATE)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Columbia: Initial GET failed: {e}")
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
        logger.info("Columbia: Initiating wildcard (%) search POST...")
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
            "SearchSortOption": "0", # Sorted by Name
            "SearchOrderOption": "0", # Ascending
            "btnSumit": "Submit",
        }

        try:
            resp2 = session.post(SEARCH_URL, data=post_data, timeout=30, verify=False, impersonate=IMPERSONATE)
            resp2.raise_for_status()
        except Exception as e:
            logger.error(f"Columbia: Wildcard POST search failed: {e}")
            raise

        soup2 = BeautifulSoup(resp2.text, "html.parser")
        initial_records = self._parse_page(soup2, seen_bookings)
        all_records.extend(initial_records)
        logger.info(f"Columbia: Initial search returned {len(initial_records)} records.")

        # Step 3: Loop calling Jail.aspx/AddMoreResults to get subsequent records
        records_loaded = len(initial_records)
        json_headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": SEARCH_URL
        }

        max_pages = 50  # Safety limit (matches ~1000 inmates)
        page_idx = 1
        
        while page_idx <= max_pages:
            logger.info(f"Columbia: Fetching page {page_idx+1} (loaded so far: {records_loaded})...")
            payload = {
                "searchVals": {
                    "FirstName": "",
                    "MiddleName": "",
                    "LastName": "%",
                    "BeginBookDate": "",
                    "EndBookDate": "",
                    "BeginReleaseDate": "",
                    "EndReleaseDate": "",
                    "TypeJailSearch": 0,
                    "RecordsLoaded": records_loaded,
                    "SortOption": 0,
                    "SortOrder": 0,
                    "IsDefault": False,
                    "DateOfBirth": "",
                    "BookingNumber": ""
                }
            }

            try:
                resp3 = session.post(ADD_MORE_URL, json=payload, headers=json_headers, timeout=30, verify=False, impersonate=IMPERSONATE)
                resp3.raise_for_status()
                
                res_data = resp3.json().get("d", {})
                results_returned = res_data.get("resultsReturned", 0)
                html_snippet = res_data.get("data", "")
                
                if results_returned == 0 or not html_snippet:
                    logger.info("Columbia: AJAX returned 0 records. Roster fully loaded.")
                    break
                    
                soup_more = BeautifulSoup(html_snippet, "html.parser")
                more_records = self._parse_page(soup_more, seen_bookings)
                all_records.extend(more_records)
                logger.info(
                    f"Columbia: Page {page_idx+1} loaded {len(more_records)} new records "
                    f"(total {len(all_records)})."
                )
                records_loaded += results_returned
                
                # Check if we hit the end
                results_attempted = res_data.get("resultsAttempted", 0)
                if results_attempted > results_returned:
                    logger.info("Columbia: Reached end of results (attempted > returned).")
                    break

                page_idx += 1
                time.sleep(0.5)  # Be gentle to the server
                
            except Exception as e:
                logger.warning(f"Columbia: AJAX page {page_idx+1} load failed: {e}")
                break

        logger.info(f"Columbia County Scrape Complete: {len(all_records)} total records")
        return all_records

    def _parse_page(self, soup, seen: set = None) -> List[ArrestRecord]:
        """Delegate to shared SmartWeb card parser (ENLARGE PHOTO safe)."""
        from scrapers.smartweb_card_parser import parse_smartweb_cards

        if seen is None:
            seen = set()
        html = str(soup)
        return parse_smartweb_cards(
            html,
            county=self.county,
            facility=FACILITY,
            detail_url=SEARCH_URL,
            seen=seen,
            state="FL",
            log_prefix="Columbia",
        )
