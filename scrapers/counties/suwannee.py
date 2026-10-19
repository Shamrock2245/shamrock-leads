"""
Suwannee County (FL) Sheriff's Office — official SmartCOP SmartWEB "JAIL View".

Source contract (re-verified 2026-09-25, docs/recon/FL_GAP_QUEUE_2026-09-25.md):
  * URL: https://smartcop.suwanneesheriff.com/smartwebclient/jail.aspx
  * Plain HTTPS ASP.NET WebForms; no login; the page's reCAPTCHA hook is
    unconfigured (empty sitekey) and is not required for the public search.
  * Supported broad criterion: "Begin/End Booking Date" + "Current Inmates Only",
    sorted by Booking Date descending. First page is the form POST; further
    pages come from ``Jail.aspx/AddMoreResults`` with ``{"searchVals": {...}}``.
  * Each card exposes the source-issued ``Booking No`` (``SCSO<YY>JBN<NNNNNN>``),
    booking date/time, name, status, bond and a charges table.
    Booking_Number is copied from the source; cards whose image ``bookno`` and
    ``Booking No:`` text disagree, or that lack one, are dropped.

Honest access and no invented values (2026-10-15): every request goes through
``scrapers.plain_http`` (honest User-Agent, certificate verified; a block raises
``egress_block:``, a real certificate failure ``tls_cert_invalid:``). A charge's
bond counts only when the source shows a dollar amount (a published 0 is kept);
any charge with a blank / NO BOND / HOLD / text bond, or no charges, makes the
booking bond ``""``. Status comes only from the card's ``Status:`` token
(In Jail / In Custody -> "In Custody", Out of Jail / Released -> "Released");
anything else is ``""``.

Why it was silent before: the old code searched ``txbLastName="%"`` (not a
supported criterion — the server answers "Please fill in at least one search
criteria" with 0 rows) and posted a flat JSON body to AddMoreResults (the
endpoint expects ``{"searchVals": ...}``).
"""

import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from scrapers.plain_http import make_session, request
from scrapers.published_bond import charge_bond_amount, total_published_bond
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://smartcop.suwanneesheriff.com/smartwebclient"
SEARCH_URL = f"{BASE_URL}/jail.aspx"
AJAX_URL = f"{BASE_URL}/Jail.aspx/AddMoreResults"
FACILITY = "Suwannee County Jail"
LOOKBACK_DAYS = 30
MAX_PAGES = 60
REQUEST_DELAY_S = 0.3

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": SEARCH_URL,
}

NO_CRITERIA_TEXT = "There are no records matching"


class SuwanneeCountyScraper(BaseScraper):
    """Suwannee County (FL) — SmartCOP JAIL View, booking-date window (Live Oak)."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Suwannee"

    @property
    def state(self) -> str:
        return "FL"

    def _search_vals(self, begin: str, end: str, loaded: int) -> dict:
        return {
            "FirstName": "", "MiddleName": "", "LastName": "",
            "BeginBookDate": begin, "EndBookDate": end,
            "BeginReleaseDate": "", "EndReleaseDate": "",
            "TypeJailSearch": 0, "RecordsLoaded": loaded,
            "SortOption": 1, "SortOrder": 1, "IsDefault": False,
            "DateOfBirth": "", "BookingNumber": "",
        }

    def scrape(self, lookback_days: Optional[int] = None) -> List[ArrestRecord]:
        days = LOOKBACK_DAYS if lookback_days is None else lookback_days
        begin = (date.today() - timedelta(days=days)).strftime("%m/%d/%Y")
        end = date.today().strftime("%m/%d/%Y")

        session = make_session(HEADERS)
        resp = request(session, "GET", SEARCH_URL, county="Suwannee", timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        if not soup.find("input", {"name": "tbBeginDate"}) or not soup.find("select", {"name": "TypeSearch"}):
            raise RuntimeError("Suwannee: JAIL View search form changed (tbBeginDate/TypeSearch missing)")
        form = {i["name"]: i.get("value", "") for i in soup.select("input[type=hidden]") if i.get("name")}
        form.update({
            "txbLastName": "", "txbFirstName": "", "txbMiddleName": "",
            "tbBeginDate": begin, "tbEndDate": end,
            "tbBeginReleaseDate": "", "tbEndReleaseDate": "",
            "TypeSearch": "0",          # Current Inmates Only
            "SearchSortOption": "1",    # Booking Date
            "SearchOrderOption": "1",   # Descending
            "btnSumit": "Submit",
        })
        resp2 = request(session, "POST", SEARCH_URL, county="Suwannee", data=form, timeout=60)
        resp2.raise_for_status()

        seen: set = set()
        all_records = self._parse_html(resp2.text, seen)
        m = re.search(r'id="ResultsReturned"[^>]*>(\d+)<', resp2.text)
        loaded = int(m.group(1)) if m else len(all_records)
        logger.info("Suwannee: first page %d cards (%s..%s)", loaded, begin, end)

        json_headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": SEARCH_URL,
        }
        page = 1
        while loaded and page < MAX_PAGES:
            time.sleep(REQUEST_DELAY_S)
            r = request(session, "POST", AJAX_URL, county="Suwannee",
                        json={"searchVals": self._search_vals(begin, end, loaded)},
                        headers=json_headers, timeout=60)
            r.raise_for_status()
            d = (r.json() or {}).get("d") or {}
            returned = int(d.get("resultsReturned") or 0)
            attempted = int(d.get("resultsAttempted") or 0)
            snippet = d.get("data") or ""
            if returned <= 0 or not snippet:
                break
            all_records.extend(self._parse_html("<table>" + snippet + "</table>", seen))
            loaded += returned
            page += 1
            if attempted and returned < attempted:
                break

        logger.info("Suwannee: %d source bookings from %d cards", len(all_records), loaded)
        return all_records

    def _parse_html(self, html: str, seen: set) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
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
            text_bk = re.search(r"Booking No:\s*([A-Z0-9]+)", block_text)
            if not text_bk or text_bk.group(1) != booking_num:
                continue

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

            bd_m = re.search(r"Booking Date:\s*([\d/]+)(?:\s+(\d{1,2}:\d{2}\s*[AP]M))?", block_text)
            booking_date = bd_m.group(1) if bd_m else ""
            booking_time = (bd_m.group(2) or "") if bd_m else ""

            # Parse status from block text
            # Bounded token only; "Out of Jail" must not read as in custody, and
            # a missing / unrecognised status is unknown (""), never invented.
            status_m = re.search(r"Status:\s*(Out\s+of\s+Jail|In\s+Jail|In\s+Custody|Released)",
                                 block_text, re.IGNORECASE)
            status_raw = status_m.group(1) if status_m else ""
            if re.search(r"released|out\s+of", status_raw, re.I):
                status = "Released"
            elif status_raw:
                status = "In Custody"
            else:
                status = ""

            # Parse address from block text
            addr_m = re.search(r"Address Given:\s*([^\n\r\t]+)", block_text)
            address = addr_m.group(1).strip() if addr_m else ""

            # Find the charges sub-table using sibling rows
            charges_list = []
            bonds: List[Optional[float]] = []  # one per charge row; None = unpublished
            charges_tables = []
            row = img.find_parent("tr")
            if row:
                sibling = row.find_next_sibling("tr")
                while sibling:
                    # Stop if we hit the next inmate card top row
                    if sibling.find("img", src=re.compile(r"bookno=")):
                        break
                    for table_el in sibling.find_all("table", class_="JailViewCharges"):
                        first_row = table_el.find("tr")
                        title = first_row.get_text(" ", strip=True).upper() if first_row else ""
                        if title.startswith("HOLDS"):
                            continue
                        charges_tables.append(table_el)
                    sibling = sibling.find_next_sibling("tr")

            for charges_table in charges_tables:
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
                        if statute or desc or bond_str:
                            bonds.append(charge_bond_amount(bond_str))

            charges_str = " | ".join(charges_list)

            records.append(ArrestRecord(
                County=self.county, State="FL", Facility=FACILITY,
                Full_Name=full_name.upper(),
                First_Name=first.upper(), Middle_Name=middle.upper(), Last_Name=last.upper(),
                DOB=dob, Race=race.upper() if race else "", Sex=sex.upper() if sex else "",
                Booking_Number=booking_num, Booking_Date=booking_date, Booking_Time=booking_time,
                Charges=charges_str, Bond_Amount=total_published_bond(bonds),
                Address=address, Status=status,
                Detail_URL=SEARCH_URL,
                Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
                LastChecked=datetime.now(timezone.utc).isoformat(),
                LastCheckedMode="INITIAL",
            ))

        return records
