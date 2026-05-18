"""
Sumter County Arrest Scraper — SmartCop AJAX (AddMoreResults)
Source: Sumter County Sheriff's Office
URL: https://portal.sumtercountysheriff.org/smartwebclient/Jail.aspx
Method: curl_cffi POST to Jail.aspx/AddMoreResults (ASP.NET PageMethods AJAX)
Fix 2026-05-18: Replaced broken form POST with AJAX endpoint (same pattern as Suwannee/Putnam)
"""
import logging
import re
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://portal.sumtercountysheriff.org/smartwebclient"
AJAX_URL = f"{BASE_URL}/Jail.aspx/AddMoreResults"
FACILITY = "Sumter County Jail"
IMPERSONATE = "chrome131"
PAGE_SIZE = 185

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Content-Type": "application/json; charset=utf-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE_URL}/Jail.aspx",
    "Origin": "https://portal.sumtercountysheriff.org",
}


class SumterCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Sumter"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cf
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("curl_cffi/bs4 not installed")
            return []

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
                "SortOption": 1,
                "SortOrder": 1,
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
                logger.error(f"Sumter AJAX failed (offset={offset}): {e}")
                break

            try:
                data = r.json()
                html_rows = data["d"]["Data"]["data"]
            except Exception as e:
                logger.error(f"Sumter JSON parse failed: {e}")
                break

            if not html_rows or len(html_rows) < 10:
                break

            batch = self._parse_rows(html_rows, seen)
            if not batch:
                break

            records.extend(batch)
            offset += PAGE_SIZE

            if offset >= PAGE_SIZE * 3:
                break

        logger.info(f"Sumter: {len(records)} records")
        return records

    def _parse_rows(self, html: str, seen: set) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        records = []

        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 10:
                continue
            texts = [td.get_text(separator=" ", strip=True) for td in cells]

            if "Booking No:" not in texts[1]:
                continue

            full_name = texts[2] if len(texts) > 2 else texts[1]
            full_name = re.sub(r'\s*\([^)]*\)\s*$', '', full_name).strip()
            if not full_name or len(full_name) < 3:
                continue

            booking_num = texts[6] if len(texts) > 6 else ""
            booking_date = texts[10].split()[0] if len(texts) > 10 and texts[10] else ""
            status = texts[4] if len(texts) > 4 else "In Custody"
            bond_raw = texts[17] if len(texts) > 17 else "0"
            address = texts[20] if len(texts) > 20 else ""

            race, sex = "", ""
            rs_match = re.search(r'\(([A-Z]+)/\s*([A-Z]+)\)', texts[1])
            if rs_match:
                race, sex = rs_match.group(1), rs_match.group(2)

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
                Booking_Date=booking_date,
                Status="In Custody" if "jail" in status.lower() or "custody" in status.lower() else status,
                Release_Date="",
                Facility=FACILITY,
                Race=race, Sex=sex,
                Address=address,
                Bond_Amount=str(self._parse_bond(bond_raw)),
                Detail_URL=f"{BASE_URL}/Jail.aspx",
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
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm) > 1 else ""), last
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
