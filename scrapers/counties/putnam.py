"""
Putnam County Arrest Scraper — SmartWeb ASP.NET
Source: Putnam County Sheriff's Office
URL: https://smartweb.pcso.us/smartwebclient/Jail.aspx
Method: requests POST — SmartWeb form with txbLastName/txbFirstName/btnSumit
Returns card-style HTML: "AKERS, SHAWN ZACHARY (W/MALE) Status: In Jail Booking No: PCSO26JBN001058..."
"""
import logging
import re
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

SEARCH_URL = "https://smartweb.pcso.us/smartwebclient/Jail.aspx"
FACILITY = "Putnam County Jail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": SEARCH_URL,
    "DNT": "1",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}
IMPERSONATE = "chrome131"


class PutnamCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Putnam"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cffi_requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("curl_cffi/bs4 not installed")
            return []

        session = cffi_requests.Session()

        # GET to get ViewState
        try:
            resp = session.get(SEARCH_URL, headers=HEADERS, timeout=30, impersonate=IMPERSONATE)
            if resp.status_code != 200:
                raise Exception(f"{resp.status_code} error")
        except Exception as e:
            logger.error(f"Putnam: GET failed: {e}")
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
        }

        try:
            resp = session.post(SEARCH_URL, data=post_data, headers=HEADERS, timeout=60, impersonate=IMPERSONATE)
            if resp.status_code != 200:
                raise Exception(f"{resp.status_code} error")
        except Exception as e:
            logger.error(f"Putnam: POST failed: {e}")
            return []

        return self._parse(resp.text)

    def _parse(self, html: str) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        records = []
        seen = set()

        # SmartWeb Putnam uses card-style blocks
        # Pattern: "LASTNAME, FIRSTNAME MIDDLENAME (RACE/SEX) Status: In Jail Booking No: PCSO26JBN001058 MniNo: ... Booking Date: 04/19/2026 03:52 AM Bond Amount: $0.00"
        full_text = soup.get_text("\n")
        # Split on booking number pattern to find individual records
        blocks = re.split(r'\n(?=[A-Z][A-Z\s,\'-]+\s*\([A-Z/]+\))', full_text)

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            # Extract name (LASTNAME, FIRSTNAME MIDDLENAME)
            name_m = re.match(r'^([A-Z][A-Z\s,\'-]+?)(?:\s*\([A-Z/]+\))', block)
            if not name_m:
                continue
            full_name = name_m.group(1).strip().rstrip(",")

            # Extract booking number
            booking_m = re.search(r'Booking\s*No[.:]?\s*([A-Z0-9]+)', block, re.I)
            booking_num = booking_m.group(1) if booking_m else ""

            # Extract booking date
            date_m = re.search(r'Booking\s*Date[.:]?\s*(\d{1,2}/\d{1,2}/\d{2,4}(?:\s+\d{1,2}:\d{2}(?:\s*[AP]M)?)?)', block, re.I)
            booking_date = date_m.group(1) if date_m else ""

            # Extract bond amount
            bond_m = re.search(r'Bond\s*Amount[.:]?\s*\$?([\d,]+\.?\d*)', block, re.I)
            bond_raw = bond_m.group(1) if bond_m else "0"

            # Extract race/sex from parentheses
            demo_m = re.search(r'\(([A-Z]+)/([A-Z]+)\)', block)
            race = demo_m.group(1) if demo_m else ""
            sex = demo_m.group(2) if demo_m else ""

            # Extract status
            status_m = re.search(r'Status[.:]?\s*([A-Za-z\s]+?)(?:\n|Booking)', block, re.I)
            status = status_m.group(1).strip() if status_m else "In Custody"
            if "jail" in status.lower() or "custody" in status.lower() or "in" in status.lower():
                status = "In Custody"

            key = booking_num or full_name
            if not key or key in seen:
                continue
            seen.add(key)

            f, m, l = self._pn(full_name)
            bond_amount = self._parse_bond(bond_raw)

            records.append(ArrestRecord(
                County=self.county,
                Booking_Number=booking_num,
                Full_Name=full_name,
                First_Name=f, Middle_Name=m, Last_Name=l,
                Booking_Date=booking_date,
                Status=status,
                Facility=FACILITY,
                Race=race,
                Sex=sex,
                Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                LastCheckedMode="INITIAL",
            ))

        # Fallback: table parse
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
                        booking_date = texts[2] if len(texts) > 2 else ""
                        key = booking_num or full_name
                        if key in seen:
                            continue
                        seen.add(key)
                        f, m, l = self._pn(full_name)
                        records.append(ArrestRecord(
                            County=self.county,
                            Booking_Number=booking_num,
                            Full_Name=full_name,
                            First_Name=f, Middle_Name=m, Last_Name=l,
                            Booking_Date=booking_date,
                            Status="In Custody",
                            Facility=FACILITY,
                            LastCheckedMode="INITIAL",
                        ))
                    if records:
                        break

        logger.info(f"Putnam: {len(records)} records")
        return records

    @staticmethod
    def _pn(n):
        if not n:
            return "", "", ""
        n = " ".join(n.strip().split())
        if "," in n:
            p = n.split(",", 1)
            l = p[0].strip()
            fm = p[1].strip().split()
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm) > 1 else ""), l
        p = n.split()
        return p[0], (" ".join(p[2:]) if len(p) > 2 else ""), (p[-1] if len(p) >= 2 else "")

    @staticmethod
    def _parse_bond(bond_str):
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", str(bond_str).strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
