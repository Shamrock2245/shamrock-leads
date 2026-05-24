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

        session = requests.Session()
        session.headers.update(HEADERS)

        # Step 1: Initial GET request to retrieve standard ASP.NET ViewState tokens
        try:
            logger.info(f"Columbia: Loading initial page from {SEARCH_URL}")
            resp = session.get(SEARCH_URL, timeout=30, verify=False)
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
            resp2 = session.post(SEARCH_URL, data=post_data, timeout=30, verify=False)
            resp2.raise_for_status()
        except Exception as e:
            logger.error(f"Columbia: Wildcard POST search failed: {e}")
            raise

        soup2 = BeautifulSoup(resp2.text, "html.parser")
        initial_records = self._parse_page(soup2)
        
        for r in initial_records:
            if r.Booking_Number not in seen_bookings:
                seen_bookings.add(r.Booking_Number)
                all_records.append(r)
                
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
                resp3 = session.post(ADD_MORE_URL, json=payload, headers=json_headers, timeout=30, verify=False)
                resp3.raise_for_status()
                
                res_data = resp3.json().get("d", {})
                results_returned = res_data.get("resultsReturned", 0)
                html_snippet = res_data.get("data", "")
                
                if results_returned == 0 or not html_snippet:
                    logger.info("Columbia: AJAX returned 0 records. Roster fully loaded.")
                    break
                    
                soup_more = BeautifulSoup(html_snippet, "html.parser")
                more_records = self._parse_page(soup_more)
                
                new_count = 0
                for r in more_records:
                    if r.Booking_Number not in seen_bookings:
                        seen_bookings.add(r.Booking_Number)
                        all_records.append(r)
                        new_count += 1
                        
                logger.info(f"Columbia: Page {page_idx+1} loaded {len(more_records)} records ({new_count} new).")
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

    def _parse_page(self, soup) -> List[ArrestRecord]:
        records = []
        
        # Uniquely locate each inmate card header cell
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
                
                first_name, middle_name, last_name = self._parse_name(full_name)

                # Now extract details inside the inmate card table
                detail_table = header_td.find_parent("table")
                if not detail_table:
                    continue
                
                detail_text = detail_table.get_text(" ", strip=True)
                
                booking_no_match = re.search(r"Booking\s+No[:\s]+([A-Z0-9]+)", detail_text, re.IGNORECASE)
                booking_number = booking_no_match.group(1).strip() if booking_no_match else ""
                if not booking_number:
                    continue
                
                booking_date_match = re.search(r"Booking\s+Date[:\s]+([\d/]+\s+[\d:]+\s*(?:AM|PM)?)", detail_text, re.IGNORECASE)
                booking_date = booking_date_match.group(1).strip() if booking_date_match else ""

                address_match = re.search(r"Address\s+Given[:\s]+(.+?)(?:HOLDS|CHARGES|$)", detail_text, re.IGNORECASE | re.DOTALL)
                address = " ".join(address_match.group(1).strip().split()) if address_match else ""

                # Charges extraction: find charges sub-table following the top-level row of this card
                charges_list = []
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
                                
                            # Parse bond
                            bond_val = self._parse_bond_val(bond_str)
                            total_bond += bond_val
                
                charges_str = " | ".join(charges_list)

                records.append(ArrestRecord(
                    County=self.county,
                    Booking_Number=booking_number,
                    Full_Name=full_name,
                    First_Name=first_name,
                    Middle_Name=middle_name,
                    Last_Name=last_name,
                    DOB=dob,
                    Booking_Date=booking_date,
                    Charges=charges_str,
                    Bond_Amount=str(int(total_bond)) if total_bond.is_integer() else f"{total_bond:.2f}",
                    Status="In Custody",
                    Detail_URL=SEARCH_URL,
                    Facility=FACILITY,
                    Race=race,
                    Sex=sex,
                    Address=address,
                    LastCheckedMode="INITIAL"
                ))
            except Exception as re_err:
                logger.warning(f"Columbia: failed to parse inmate row: {re_err}")
                continue
                
        return records

    @staticmethod
    def _parse_name(name_str: str):
        if not name_str:
            return "", "", ""
        name_str = " ".join(name_str.strip().split())
        if "," in name_str:
            parts = name_str.split(",", 1)
            last = parts[0].strip()
            fm = parts[1].strip().split()
            first = fm[0] if fm else ""
            middle = " ".join(fm[1:]) if len(fm) > 1 else ""
            return first, middle, last
        parts = name_str.split()
        return parts[0], "", parts[-1] if len(parts) >= 2 else ""

    @staticmethod
    def _parse_bond_val(bond_str: str) -> float:
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
