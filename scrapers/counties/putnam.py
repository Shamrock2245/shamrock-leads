"""
Putnam County Arrest Scraper — SmartCop SmartWeb AJAX
Source: Putnam County Sheriff's Office
URL: https://smartweb.pcso.us/smartwebclient/Jail.aspx
Method: curl_cffi POST to Jail.aspx/AddMoreResults JSON endpoint

Fix 2026-05-18: Old scraper used wrong form POST (txbLastName etc).
                SmartCop uses JSON AJAX: POST Jail.aspx/AddMoreResults
                with the full SearchVals object (IsDefault=True for full roster).
                Returns d.Data.data as HTML rows with booking numbers in img src.
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import List

from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://smartweb.pcso.us/smartwebclient/Jail.aspx"
AJAX_URL = "https://smartweb.pcso.us/smartwebclient/Jail.aspx/AddMoreResults"
FACILITY = "Putnam County Jail"
IMPERSONATE = "chrome131"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Content-Type": "application/json; charset=utf-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": BASE_URL,
}

SEARCH_VALS_BASE = {
    "FirstName": "", "MiddleName": "", "LastName": "",
    "BeginBookDate": "", "EndBookDate": "",
    "BeginReleaseDate": "", "EndReleaseDate": "",
    "TypeJailSearch": 0, "RecordsLoaded": 0,
    "SortOption": 0, "SortOrder": 0, "IsDefault": True,
}


class PutnamCountyScraper(BaseScraper):
    """Putnam County (FL) — SmartCop AJAX roster"""

    @property
    def county(self) -> str:
        return "Putnam"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cf
        except ImportError:
            logger.error("Putnam: curl_cffi not installed")
            raise

        session = cf.Session()
        records: List[ArrestRecord] = []
        seen: set = set()
        offset = 0

        while True:
            payload = {**SEARCH_VALS_BASE, "RecordsLoaded": offset}
            try:
                r = session.post(
                    AJAX_URL, headers=HEADERS,
                    data=json.dumps(payload),
                    timeout=20, impersonate=IMPERSONATE,
                )
                if r.status_code != 200:
                    logger.warning(f"Putnam: HTTP {r.status_code} at offset {offset}")
                    break

                d = r.json()
                inner = d.get("d", {})
                if isinstance(inner, dict):
                    inner = inner.get("Data", inner)
                html = inner.get("data", "") if isinstance(inner, dict) else ""
                returned = inner.get("resultsReturned", 0) if isinstance(inner, dict) else 0

                if not html or returned == 0:
                    break

                batch = self._parse_html(html, seen)
                records.extend(batch)
                offset += returned
                if returned < 20:
                    break

            except Exception as e:
                logger.warning(f"Putnam: error at offset {offset}: {e}")
                break

        logger.info(f"Putnam: {len(records)} records")
        return records

    def _parse_html(self, html: str, seen: set) -> List[ArrestRecord]:
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

            # Collect text from this row and next 15 siblings
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

            # Name: "LAST, FIRST MIDDLE (RACE/SEX)"
            name_m = re.search(
                r"([A-Z][A-Z\s\-\',]+,\s*[A-Z][A-Z\s\-\']+)\s*\(([A-Z])/\s*([A-Z]+)\)",
                block_text,
            )
            full_name = name_m.group(1).strip() if name_m else ""
            race = name_m.group(2) if name_m else ""
            sex = name_m.group(3) if name_m else ""

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

            charges = " | ".join(re.findall(r"Charge(?:\s+\d+)?:\s*([^\n\r]+)", block_text))

            bond_m = re.search(r"Bond[^:]*:\s*\$?([\d,\.]+)", block_text)
            bond = bond_m.group(1).replace(",", "") if bond_m else "0"

            # Parse status from block text
            status_m = re.search(r"Status:\s*([a-zA-Z\s]+)", block_text)
            status = status_m.group(1).strip() if status_m else "In Custody"
            if "jail" in status.lower() or "custody" in status.lower():
                status = "In Custody"

            # Parse address from block text
            addr_m = re.search(r"Address Given:\s*([^\n\r\t]+)", block_text)
            address = addr_m.group(1).strip() if addr_m else ""

            if not full_name:
                continue

            records.append(ArrestRecord(
                County=self.county, State="FL", Facility=FACILITY,
                Full_Name=full_name.upper(),
                First_Name=first.upper(), Middle_Name=middle.upper(), Last_Name=last.upper(),
                DOB=dob, Race=race, Sex=sex,
                Booking_Number=booking_num, Booking_Date=booking_date,
                Charges=charges, Bond_Amount=bond,
                Address=address, Status=status,
                Detail_URL=BASE_URL,
                Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
                LastChecked=datetime.now(timezone.utc).isoformat(),
                LastCheckedMode="INITIAL",
            ))

        return records
