"""
Santa Rosa County Arrest Scraper — SmartWeb ASP.NET
Source: Santa Rosa County Sheriff's Office
URL: https://jailview.srso.net/SmartWebClient/jail.aspx
Method: requests POST — SmartWeb form (same pattern as Putnam/Suwannee)
Fields: Last Name, First Name, Middle Name, Booking Date Range, Release Date Range,
        Current Inmates Only, Released Inmates Only, Both Current And Released
"""

import logging
import re
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

SEARCH_URL = "https://jailview.srso.net/SmartWebClient/jail.aspx"
FACILITY = "Santa Rosa County Jail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": SEARCH_URL,
    "DNT": "1",
    "Connection": "keep-alive",
}
IMPERSONATE = "chrome131"


class SantaRosaCountyScraper(BaseScraper):
    """Santa Rosa County (FL) — SmartWeb jail roster (Milton/Pensacola area)"""

    @property
    def county(self) -> str:
        return "Santa Rosa"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cffi_requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("curl_cffi/bs4 not installed")
            return []

        session = cffi_requests.Session()

        try:
            resp = session.get(SEARCH_URL, headers=HEADERS, timeout=30, impersonate=IMPERSONATE)
            if resp.status_code != 200:
                raise Exception(f"GET {resp.status_code}")
        except Exception as e:
            logger.error(f"Santa Rosa GET failed: {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        viewstate = soup.find("input", {"name": "__VIEWSTATE"})
        viewstate_gen = soup.find("input", {"name": "__VIEWSTATEGENERATOR"})
        event_val = soup.find("input", {"name": "__EVENTVALIDATION"})

        post_data = {
            "__VIEWSTATE": viewstate["value"] if viewstate else "",
            "__VIEWSTATEGENERATOR": viewstate_gen["value"] if viewstate_gen else "",
            "__EVENTVALIDATION": event_val["value"] if event_val else "",
            "txbLastName": "",
            "txbFirstName": "",
            "btnSumit": "Search",
            "rdoCurrent": "rbCurrentInmates",
        }

        try:
            resp = session.post(SEARCH_URL, data=post_data, headers=HEADERS, timeout=60, impersonate=IMPERSONATE)
            if resp.status_code != 200:
                raise Exception(f"POST {resp.status_code}")
        except Exception as e:
            logger.error(f"Santa Rosa POST failed: {e}")
            return []

        records = self._parse(resp.text)
        logger.info(f"Santa Rosa: {len(records)} records")
        return records

    def _parse(self, html: str) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        records = []
        seen = set()

        full_text = soup.get_text("\n")
        blocks = re.split(r'\n(?=[A-Z][A-Z\s,\'-]+\s*\([A-Z/]+\))', full_text)

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            name_m = re.match(r'^([A-Z][A-Z\s,\'-]+?)(?:\s*\([A-Z/]+\))', block)
            if not name_m:
                continue
            full_name = name_m.group(1).strip().rstrip(",")

            booking_m = re.search(r'Booking\s*No[.:]?\s*([A-Z0-9]+)', block, re.I)
            booking_num = booking_m.group(1) if booking_m else ""

            date_m = re.search(r'Booking\s*Date[.:]?\s*(\d{1,2}/\d{1,2}/\d{2,4}(?:\s+\d{1,2}:\d{2}(?:\s*[AP]M)?)?)', block, re.I)
            booking_date = date_m.group(1) if date_m else ""

            bond_m = re.search(r'Bond\s*Amount[.:]?\s*\$?([\d,]+\.?\d*)', block, re.I)
            bond_raw = bond_m.group(1) if bond_m else "0"

            demo_m = re.search(r'\(([A-Z]+)/([A-Z]+)\)', block)
            race = demo_m.group(1) if demo_m else ""
            sex = demo_m.group(2) if demo_m else ""

            status_m = re.search(r'Status[.:]?\s*([A-Za-z\s]+?)(?:\n|Booking)', block, re.I)
            status = status_m.group(1).strip() if status_m else "In Custody"
            if "jail" in status.lower() or "custody" in status.lower() or "in" in status.lower():
                status = "In Custody"

            charge_descs = re.findall(r'Charge[.:]?\s*([^\n]+)', block, re.I)
            charges_str = " | ".join(c.strip() for c in charge_descs if c.strip())

            key = booking_num or full_name
            if not key or key in seen:
                continue
            seen.add(key)

            f, m, l = self._parse_name(full_name)

            records.append(ArrestRecord(
                County=self.county,
                Booking_Number=booking_num,
                Full_Name=full_name,
                First_Name=f, Middle_Name=m, Last_Name=l,
                        DOB="",
                Booking_Date=booking_date,
                Status=status,
                        Release_Date="",
                Facility=FACILITY,
                Race=race,
                Sex=sex,
                Charges=charges_str,
                Bond_Amount=str(self._parse_bond(bond_raw)) if bond_raw else "0",
                Detail_URL=SEARCH_URL,

                LastCheckedMode="INITIAL",
            ))

        # Table fallback
        if not records:
            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                if len(rows) < 2:
                    continue
                header_text = rows[0].get_text(" ").lower()
                if "name" in header_text and ("booking" in header_text or "inmate" in header_text):
                    for row in rows[1:]:
                        cells = row.find_all("td")
                        if len(cells) < 2:
                            continue
                        texts = [c.get_text(strip=True) for c in cells]
                        full_name = texts[0]
                        if not full_name:
                            continue
                        booking_num = texts[1] if len(texts) > 1 else ""
                        key = booking_num or full_name
                        if key in seen:
                            continue
                        seen.add(key)
                        f, m, l = self._parse_name(full_name)
                        records.append(ArrestRecord(
                            County=self.county,
                            Booking_Number=booking_num,
                            Full_Name=full_name,
                            First_Name=f, Middle_Name=m, Last_Name=l,
                        DOB="",
                            Booking_Date=texts[2] if len(texts) > 2 else "",
                            Status="In Custody",
                        Release_Date="",
                            Facility=FACILITY,
                            Detail_URL=SEARCH_URL,

                            LastCheckedMode="INITIAL",
                        ))
                    if records:
                        break

        return records

    @staticmethod
    def _parse_name(name: str):
        if not name:
            return "", "", ""
        name = " ".join(name.strip().split())
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip()
            fm = parts[1].strip().split()
            first = fm[0] if fm else ""
            middle = " ".join(fm[1:]) if len(fm) > 1 else ""
            return first, middle, last
        parts = name.split()
        if len(parts) == 1:
            return parts[0], "", ""
        if len(parts) == 2:
            return parts[0], "", parts[1]
        return parts[0], " ".join(parts[1:-1]), parts[-1]

    @staticmethod
    def _parse_bond(bond_str: str) -> float:
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", str(bond_str).strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
