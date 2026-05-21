"""
Suwannee County Arrest Scraper — SmartCop AJAX (AddMoreResults)
Source: Suwannee County Sheriff's Office
URL: https://smartcop.suwanneesheriff.com/smartwebclient/jail.aspx
Method: curl_cffi POST to jail.aspx/AddMoreResults (ASP.NET PageMethods AJAX)
Fields: Name, Booking No, MniNo, Booking Date, Age, Bond Amount, Address, Status

Fix 2026-05-18: The form POST only sets up JS state; actual data is loaded via
                jail.aspx/AddMoreResults JSON endpoint returning HTML rows.
                185 records per call, paginated via RecordsLoaded offset.
"""

import json
import logging
import re
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://smartcop.suwanneesheriff.com/smartwebclient"
AJAX_URL = f"{BASE_URL}/jail.aspx/AddMoreResults"
DETAIL_URL = f"{BASE_URL}/jail.aspx"
FACILITY = "Suwannee County Jail"
IMPERSONATE = "chrome131"
PAGE_SIZE = 185  # SmartCop default batch size

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Content-Type": "application/json; charset=utf-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE_URL}/jail.aspx",
    "Origin": "https://smartcop.suwanneesheriff.com",
}


class SuwanneeCountyScraper(BaseScraper):
    """Suwannee County (FL) — SmartCop AJAX jail roster (Live Oak)"""

    @property
    def county(self) -> str:
        return "Suwannee"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cf
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("curl_cffi/bs4 not installed")
            raise

        session = cf.Session()
        records = []
        seen = set()
        offset = 0

        while True:
            payload = {
                "FirstName": "",
                "MiddleName": "",
                "LastName": "",
                "BeginBookDate": "",
                "EndBookDate": "",
                "BeginReleaseDate": "",
                "EndReleaseDate": "",
                "TypeJailSearch": 0,
                "RecordsLoaded": offset,
                "SortOption": 1,   # 1 = BookingDate
                "SortOrder": 1,    # 1 = Descending
                "IsDefault": False,
            }

            try:
                r = session.post(
                    AJAX_URL,
                    json=payload,
                    headers=HEADERS,
                    timeout=30,
                    impersonate=IMPERSONATE,
                )
                r.raise_for_status()
            except Exception as e:
                logger.error(f"Suwannee AJAX failed (offset={offset}): {e}")
                break

            try:
                data = r.json()
                html_rows = data["d"]["Data"]["data"]
            except Exception as e:
                logger.error(f"Suwannee JSON parse failed: {e}")
                break

            if not html_rows or len(html_rows) < 10:
                break

            batch = self._parse_html(html_rows, seen)
            if not batch:
                break

            records.extend(batch)
            offset += PAGE_SIZE

            # SmartCop typically has < 300 inmates; stop after 2 pages
            if offset >= PAGE_SIZE * 2:
                break

        logger.info(f"Suwannee: {len(records)} records")
        return records

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
                re.IGNORECASE
            )
            full_name = name_m.group(1).strip() if name_m else ""
            race = name_m.group(2) if name_m else ""
            sex = name_m.group(3) if name_m else ""

            # Try relaxed name regex if strict one failed
            if not full_name:
                name_m = re.search(
                    r"([a-zA-Z][a-zA-Z\s\-\',]+,\s*[a-zA-Z][a-zA-Z\s\-\']+)\s*\(([a-zA-Z])/\s*([a-zA-Z]+)\)",
                    block_text
                )
                if name_m:
                    full_name = name_m.group(1).strip()
                    race = name_m.group(2)
                    sex = name_m.group(3)

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
                DOB=dob, Race=race.upper() if race else "", Sex=sex.upper() if sex else "",
                Booking_Number=booking_num, Booking_Date=booking_date,
                Charges=charges, Bond_Amount=bond,
                Address=address, Status=status,
                Detail_URL=DETAIL_URL,
                Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
                LastChecked=datetime.now(timezone.utc).isoformat(),
                LastCheckedMode="INITIAL",
            ))

        return records
