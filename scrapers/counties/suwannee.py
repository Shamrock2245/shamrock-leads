"""
Suwannee County Arrest Scraper — SmartCop AJAX (AddMoreResults)
Source: Suwannee County Sheriff's Office
URL: https://smartcop.suwanneesheriff.com/smartwebclient/jail.aspx
Method: requests + BeautifulSoup — Wildcard (%) search + direct AJAX AddMoreResults loop.
Fields: Name, Booking No, MniNo, Booking Date, Age, Bond Amount, Address, Status
"""

import json
import logging
import re
import time
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://smartcop.suwanneesheriff.com/smartwebclient"
SEARCH_URL = f"{BASE_URL}/jail.aspx"
AJAX_URL = f"{BASE_URL}/jail.aspx/AddMoreResults"
FACILITY = "Suwannee County Jail"
PAGE_SIZE = 185  # SmartCop default batch size

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": SEARCH_URL,
}

class SuwanneeCountyScraper(BaseScraper):
    """Suwannee County (FL) — SmartCop AJAX jail roster (Live Oak)"""

    @property
    def county(self) -> str:
        return "Suwannee"

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
            logger.info(f"Suwannee: Loading initial page from {SEARCH_URL}")
            resp = session.get(SEARCH_URL, timeout=30, verify=False, impersonate=IMPERSONATE)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Suwannee: Initial GET failed: {e}")
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
        logger.info("Suwannee: Initiating wildcard (%) search POST...")
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
            logger.error(f"Suwannee: Wildcard POST search failed: {e}")
            raise

        initial_records = self._parse_html(resp2.text, seen_bookings)
        all_records.extend(initial_records)
        logger.info(f"Suwannee: Initial search returned {len(initial_records)} records.")

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
            logger.info(f"Suwannee: Fetching page {page_idx+1} (loaded so far: {records_loaded})...")
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
                    logger.info("Suwannee: AJAX returned 0 records. Roster fully loaded.")
                    break

                more_records = self._parse_html(html_snippet, seen_bookings)
                all_records.extend(more_records)
                logger.info(f"Suwannee: Page {page_idx+1} loaded {len(more_records)} records.")
                
                records_loaded += results_returned

                results_attempted = res_data.get("resultsAttempted", 0) if isinstance(res_data, dict) else 0
                if results_attempted > results_returned:
                    logger.info("Suwannee: Reached end of results (attempted > returned).")
                    break

                page_idx += 1
                time.sleep(0.5)

            except Exception as e:
                logger.warning(f"Suwannee: AJAX page {page_idx+1} load failed: {e}")
                break

        logger.info(f"Suwannee County Scrape Complete: {len(all_records)} total records")
        return all_records

    def _parse_html(self, html: str, seen: set) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
        from datetime import datetime, timezone
        soup = BeautifulSoup(html, "html.parser")
        records = []

        for img in soup.find_all("img", src=re.compile(r"bookno=")):
            src = img.get("src", "")
            bk_m = re.search(r"bookno=([A-Z0-9]+)", src)
            if not bk_m:
                continue
            booking_num = bk_m.group(1)
            if booking_num in seen:
                continue
            seen.add(booking_num)

            # Collect text from this row and next 15 siblings to get metadata
            block_text = ""
            try:
                row = img.find_parent("tr")
                current = row
                for _ in range(15):
                    if current:
                        block_text += " " + current.get_text(" ", strip=True)
                        current = current.find_next_sibling("tr")
            except Exception:
                pass

            block_text = " ".join(block_text.split())

            # Name, Race, Sex parsing on normalized text
            name_m = re.search(
                r"([A-Z][A-Z\s\-\',]+,\s*[A-Z][A-Z\s\-\'\.]+)\s*\(([A-Z])/\s*([A-Z]+)\s*\)",
                block_text,
                re.IGNORECASE
            )
            full_name = name_m.group(1).strip() if name_m else ""
            race = name_m.group(2) if name_m else ""
            sex_raw = name_m.group(3) if name_m else ""
            sex = "M" if sex_raw.upper() in ("MALE", "M") else "F" if sex_raw.upper() in ("FEMALE", "F") else ""

            if not full_name:
                continue

            last, first, middle = "", "", ""
            if "," in full_name:
                parts = full_name.split(",", 1)
                last = parts[0].strip()
                fm = parts[1].strip().split()
                first = fm[0] if fm else ""
                middle = " ".join(fm[1:]) if len(fm) > 1 else ""

            dob_m = re.search(r"DOB:\s*([\d/]+)", block_text)
            dob = dob_m.group(1) if dob_m else ""

            bd_m = re.search(r"Booking Date:\s*([\d/]+)", block_text)
            booking_date = bd_m.group(1) if bd_m else ""

            # Parse status from block text
            status_m = re.search(r"Status:\s*([a-zA-Z\s]+)", block_text)
            status = status_m.group(1).strip() if status_m else "In Custody"
            if "jail" in status.lower() or "custody" in status.lower():
                status = "In Custody"

            # Parse address from block text
            addr_m = re.search(r"Address Given:\s*([^\n\r\t]+)", block_text)
            address = addr_m.group(1).strip() if addr_m else ""

            # Find the charges sub-table using sibling rows
            charges_list = []
            total_bond = 0.0
            charges_table = None
            row = img.find_parent("tr")
            if row:
                sibling = row.find_next_sibling("tr")
                while sibling:
                    # Stop if we hit the next inmate card top row
                    if sibling.find("img", src=re.compile(r"bookno=")):
                        break
                    table_el = sibling.find("table", class_="JailViewCharges")
                    if table_el:
                        charges_table = table_el
                        break
                    sibling = sibling.find_next_sibling("tr")

            if charges_table:
                chg_rows = charges_table.find_all("tr")
                for chg_row in chg_rows:
                    if chg_row.get("class") and "SearchHeader" in chg_row.get("class"):
                        continue
                    cells = chg_row.find_all("td")
                    if len(cells) >= 6:
                        statute = cells[1].get_text(strip=True)
                        desc = cells[3].get_text(strip=True)
                        bond_str = cells[6].get_text(strip=True) if len(cells) >= 7 else ""
                        if statute or desc:
                            item = f"{statute} - {desc}" if statute and desc else statute or desc
                            charges_list.append(item)
                        # Parse bond
                        bond_val = 0.0
                        if bond_str:
                            cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
                            if not any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
                                try:
                                    bond_val = float(cleaned)
                                except ValueError:
                                    pass
                        total_bond += bond_val

            charges_str = " | ".join(charges_list)

            records.append(ArrestRecord(
                County=self.county, State="FL", Facility=FACILITY,
                Full_Name=full_name.upper(),
                First_Name=first.upper(), Middle_Name=middle.upper(), Last_Name=last.upper(),
                DOB=dob, Race=race.upper() if race else "", Sex=sex.upper() if sex else "",
                Booking_Number=booking_num, Booking_Date=booking_date,
                Charges=charges_str, Bond_Amount=str(int(total_bond)) if total_bond.is_integer() else f"{total_bond:.2f}",
                Address=address, Status=status,
                Detail_URL=SEARCH_URL,
                Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
                LastChecked=datetime.now(timezone.utc).isoformat(),
                LastCheckedMode="INITIAL",
            ))

        return records
