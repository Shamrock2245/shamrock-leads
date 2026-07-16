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

        session = requests.Session()
        session.headers.update(HEADERS)

        # Step 1: Initial GET request to retrieve standard ASP.NET ViewState tokens
        try:
            logger.info(f"Santa Rosa: Loading initial page from {SEARCH_URL}")
            resp = session.get(SEARCH_URL, timeout=30, verify=False)
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
            resp2 = session.post(SEARCH_URL, data=post_data, timeout=30, verify=False)
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
                resp3 = session.post(AJAX_URL, json=payload, headers=json_headers, timeout=30, verify=False)
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
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        records = []

        # SmartCop SmartWeb pattern: cards are identified by 'td.SearchHeader'
        headers = soup.find_all("td", class_="SearchHeader")
        for header_td in headers:
            try:
                header_text = header_td.get_text(" ", strip=True)
                # Parse format: "LAST, FIRST MIDDLE (R/SEX / DOB: MM/DD/YYYY )"
                header_match = re.search(
                    r"([A-Z\s,'\-\.]+)\s*\(([A-Z])/\s*(MALE|FEMALE|M|F)\s*/\s*DOB:\s*([\d/]+)\s*\)",
                    header_text,
                    re.IGNORECASE
                )
                if not header_match:
                    continue
                
                full_name = header_match.group(1).strip()
                race = header_match.group(2).strip()
                sex_raw = header_match.group(3).strip()
                sex = "M" if sex_raw.upper() in ("MALE", "M") else "F"
                dob = header_match.group(4).strip()
                
                # Split name
                last, first, middle = "", "", ""
                if "," in full_name:
                    parts = full_name.split(",", 1)
                    last = parts[0].strip()
                    fm = parts[1].strip().split()
                    first = fm[0] if fm else ""
                    middle = " ".join(fm[1:]) if len(fm) > 1 else ""

                # Now extract details inside the inmate card table
                detail_table = header_td.find_parent("table")
                if not detail_table:
                    continue
                
                detail_text = detail_table.get_text(" ", strip=True)
                
                # Extract Booking Number
                booking_no_match = re.search(r"Booking\s+No[:\s]+([A-Z0-9]+)", detail_text, re.IGNORECASE)
                booking_number = booking_no_match.group(1).strip() if booking_no_match else ""
                if not booking_number or booking_number in seen:
                    continue
                seen.add(booking_number)
                
                # Extract Booking Date
                booking_date_match = re.search(r"Booking\s+Date[:\s]+([\d/]+\s+[\d:]+\s*(?:AM|PM)?)", detail_text, re.IGNORECASE)
                booking_date = booking_date_match.group(1).strip() if booking_date_match else ""

                # Extract Address
                address_match = re.search(r"Address\s+Given[:\s]+(.+?)(?:HOLDS|CHARGES|$)", detail_text, re.IGNORECASE | re.DOTALL)
                address = " ".join(address_match.group(1).strip().split()) if address_match else ""

                # Extract Status
                status_match = re.search(r"Status[:\s]+([A-Z\s]+)", detail_text, re.IGNORECASE)
                status = status_match.group(1).strip() if status_match else "In Custody"
                if "jail" in status.lower() or "custody" in status.lower():
                    status = "In Custody"

                # Charges extraction: find charges sub-table following the top-level row of this card
                charges_list = []
                bond_types = []
                total_bond = 0.0
                
                charges_table = None
                top_row = detail_table.find_parent("tr")
                if top_row:
                    sibling = top_row.find_next_sibling("tr")
                    while sibling:
                        # Stop if we hit the next inmate card top row
                        next_header = sibling.find("td", class_="SearchHeader")
                        if next_header and "DOB:" in next_header.get_text():
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
                        # Charges rows have 7 columns: Expander, Statute, Court Case Number, Charge, Degree, Level, Bond
                        if len(cells) >= 6:
                            statute = cells[1].get_text(strip=True)
                            desc = cells[3].get_text(strip=True)
                            bond_str = cells[6].get_text(strip=True) if len(cells) >= 7 else ""
                            
                            if statute or desc:
                                item = f"{statute} - {desc}" if statute and desc else statute or desc
                                charges_list.append(item)
                                
                            # Parse bond and type
                            bond_val = 0.0
                            if bond_str:
                                cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
                                if not any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
                                    try:
                                        bond_val = float(cleaned)
                                        bond_types.append("SURETY")
                                    except ValueError:
                                        pass
                                elif "NOBOND" in cleaned or "HOLD" in cleaned:
                                    bond_types.append("NO BOND")
                            total_bond += bond_val
                
                charges_str = " | ".join(charges_list)
                bond_type = " / ".join(set(bond_types)) if bond_types else "CASH/SURETY"

                records.append(ArrestRecord(
                    County=self.county, State="FL", Facility=FACILITY,
                    Full_Name=full_name.upper(),
                    First_Name=first.upper(), Middle_Name=middle.upper(), Last_Name=last.upper(),
                    DOB=dob, Race=race.upper() if race else "", Sex=sex.upper() if sex else "",
                    Booking_Number=booking_number, Booking_Date=booking_date,
                    Charges=charges_str, 
                    Bond_Amount=str(int(total_bond)) if total_bond.is_integer() else f"{total_bond:.2f}",
                    Bond_Type=bond_type,
                    Address=address, Status=status,
                    Detail_URL=SEARCH_URL,
                    Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
                    LastChecked=datetime.now(timezone.utc).isoformat(),
                    LastCheckedMode="INITIAL",
                ))
            except Exception as e:
                logger.warning(f"Santa Rosa: failed to parse inmate row: {e}")
                continue

        return records
