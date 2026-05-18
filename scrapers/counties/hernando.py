"""
Hernando County Arrest Scraper — ASP.NET JailSearch
Source: Hernando County Sheriff's Office
URL: https://www.hernandosheriff.org/jail/Applications/JailSearch/
Method: curl_cffi POST — ASP.NET WebForms with ViewState
Fields: Name, Race, Sex, DOB, Booking Number, Booking Date, Offenses

Fix 2026-05-18: Replaced DrissionPage with curl_cffi POST.
                Results are in Table 5 (last large table, 100+ rows).
                Cell 1 contains: "LAST, FIRST RACE/SEX- DOB BOOKING_NO"
                Cell 2 = Booking Date, Cell 3 = Offenses
"""

import logging
import re
from datetime import datetime, timedelta
from typing import List
from urllib.parse import urljoin

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.hernandosheriff.org"
SEARCH_URL = f"{BASE_URL}/jail/Applications/JailSearch/"
FACILITY = "Hernando County Jail"
IMPERSONATE = "chrome131"
DAYS_BACK = 7

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": SEARCH_URL,
}


class HernandoCountyScraper(BaseScraper):
    """Hernando County (FL) — ASP.NET JailSearch (curl_cffi POST)"""

    @property
    def county(self) -> str:
        return "Hernando"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cf
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("curl_cffi/bs4 not installed")
            return []

        session = cf.Session()

        # Step 1: GET to retrieve ASP.NET ViewState tokens
        try:
            r = session.get(SEARCH_URL, headers=HEADERS, timeout=20, impersonate=IMPERSONATE)
            r.raise_for_status()
        except Exception as e:
            logger.error(f"Hernando GET failed: {e}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")

        def _val(name):
            tag = soup.find("input", {"name": name})
            return tag["value"] if tag and tag.get("value") else ""

        # Step 2: POST with date range (last DAYS_BACK days)
        today = datetime.now()
        from_date = today - timedelta(days=DAYS_BACK)

        payload = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__LASTFOCUS": "",
            "__VIEWSTATE": _val("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _val("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": _val("__EVENTVALIDATION"),
            "ctl00$ContentPlaceHolder1$tbFirstName": "",
            "ctl00$ContentPlaceHolder1$tbLastName": "",
            "ctl00$ContentPlaceHolder1$tbBookingDateFrom": from_date.strftime("%m/%d/%Y"),
            "ctl00$ContentPlaceHolder1$tbBookingDateTo": today.strftime("%m/%d/%Y"),
            "ctl00$ContentPlaceHolder1$tbReleaseDate": "",
            "ctl00$ContentPlaceHolder1$cbShowReleased": "on",
            "ctl00$ContentPlaceHolder1$btnSearch": "Search...",
        }

        try:
            r2 = session.post(SEARCH_URL, data=payload, headers=HEADERS, timeout=30, impersonate=IMPERSONATE)
            r2.raise_for_status()
        except Exception as e:
            logger.error(f"Hernando POST failed: {e}")
            return []

        records = self._parse(r2.text)
        logger.info(f"Hernando: {len(records)} records")
        return records

    def _parse(self, html: str) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        records = []
        seen = set()

        # Results are in the LAST large table (Table 5, 100+ rows)
        # Header row: ['', 'Inmate Name Race/Sex/DOB Booking Number', 'Booking Date', 'Offenses', 'Image']
        result_table = None
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 5:
                continue
            # Check header row for "Inmate Name" or "Booking Number"
            header_text = rows[0].get_text(" ").lower() if rows else ""
            if "inmate" in header_text or "booking number" in header_text or "offenses" in header_text:
                result_table = table
                # Keep going — we want the LAST matching table (the results, not the form)
        
        if not result_table:
            logger.warning("Hernando: no results table found")
            return []

        rows = result_table.find_all("tr")
        for row in rows[1:]:  # Skip header
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            # Cell 1: "LAST, FIRST RACE/SEX- DOB BOOKING_NO"
            cell1 = cells[1].get_text(separator=" ", strip=True) if len(cells) > 1 else ""
            cell2 = cells[2].get_text(strip=True) if len(cells) > 2 else ""  # Booking Date
            cell3 = cells[3].get_text(separator=" | ", strip=True) if len(cells) > 3 else ""  # Offenses

            if not cell1 or len(cell1) < 5:
                continue

            # Parse cell1: "CURL, CODY DEAN W/M- 08/31/1990 HCSO26JBN002500"
            # Booking number pattern: letters+digits
            bn_match = re.search(r'\b([A-Z]{2,6}\d{2}[A-Z]{2,3}\d{6,})\b', cell1)
            booking_num = bn_match.group(1) if bn_match else ""

            # DOB pattern
            dob_match = re.search(r'(\d{2}/\d{2}/\d{4})', cell1)
            dob = dob_match.group(1) if dob_match else ""

            # Race/Sex: W/M, B/F, H/M etc.
            rs_match = re.search(r'\b([A-Z])/([MF])\b', cell1)
            race = rs_match.group(1) if rs_match else ""
            sex = rs_match.group(2) if rs_match else ""

            # Name: everything before the race/sex marker
            name_part = cell1
            if rs_match:
                name_part = cell1[:rs_match.start()].strip()
            elif dob_match:
                name_part = cell1[:dob_match.start()].strip()
            elif booking_num:
                name_part = cell1[:cell1.find(booking_num)].strip()

            full_name = " ".join(name_part.split())
            if not full_name or len(full_name) < 3:
                continue

            # Booking date: "05/11/202601:25" → normalize
            booking_date = cell2
            if booking_date:
                # Remove time component if concatenated without space
                bd_match = re.match(r'(\d{2}/\d{2}/\d{4})', booking_date)
                if bd_match:
                    booking_date = bd_match.group(1)

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
                DOB=dob,
                Booking_Date=booking_date,
                Status="In Custody",
                Release_Date="",
                Facility=FACILITY,
                Race=race,
                Sex=sex,
                Charges=cell3,
                Bond_Amount="0",
                Detail_URL=SEARCH_URL,
                LastCheckedMode="INITIAL",
            ))

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
