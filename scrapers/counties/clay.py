"""
Clay County Arrest Scraper — Custom HTML Roster
Source: Clay County Sheriff's Office
URL: https://www.sheriffclayco.org/divisions/detention/detention-listings/
Method: requests GET — HTML table, updated every 4 hours
Fields: Name, Booking Date, Court, Court Date
"""

import logging
import re
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.sheriffclayco.org/divisions/detention/detention-listings/"
FACILITY = "Clay County Jail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sheriffclayco.org/",
}


class ClayCountyScraper(BaseScraper):
    """Clay County (FL) — Custom HTML detention listing (Green Cove Springs)"""

    @property
    def county(self) -> str:
        return "Clay"

    def scrape(self) -> List[ArrestRecord]:
        # Fix 2026-05-18: switched to curl_cffi to bypass Cloudflare-lite blocks
        # Also fixed duplicate-row issue (Clay SO page lists each inmate twice)
        try:
            from curl_cffi import requests as cf
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("curl_cffi/bs4 not installed")
            raise

        try:
            session = cf.Session()
            resp = session.get(ROSTER_URL, headers=HEADERS, timeout=30, impersonate="chrome131")
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Clay: fetch failed: {e}")
            raise

        soup = BeautifulSoup(resp.text, "html.parser")
        records = self._parse(soup)
        logger.info(f"Clay: {len(records)} records")
        return records

    def _parse(self, soup) -> List[ArrestRecord]:
        records = []
        seen = set()

        # Clay SO uses a WordPress table or list
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_text = rows[0].get_text(" ").lower()
            if not any(k in header_text for k in ["name", "inmate", "booking", "detainee"]):
                continue

            headers = [th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])]
            col = {h: i for i, h in enumerate(headers)}

            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if not cells:
                    continue

                # Try to find name column
                name_idx = col.get("name", col.get("inmate name", col.get("detainee", 0)))
                full_name = cells[name_idx] if name_idx < len(cells) else cells[0]
                if not full_name or len(full_name) < 3:
                    continue

                # Booking date
                bd_idx = None
                for k in ["booking date", "booking", "date booked", "arrest date"]:
                    if k in col:
                        bd_idx = col[k]
                        break
                booking_date = cells[bd_idx] if bd_idx is not None and bd_idx < len(cells) else ""

                # Court date
                cd_idx = None
                for k in ["court date", "court", "next court"]:
                    if k in col:
                        cd_idx = col[k]
                        break
                court_date = cells[cd_idx] if cd_idx is not None and cd_idx < len(cells) else ""

                # Booking number (may not be present)
                bn_idx = None
                for k in ["booking #", "booking no", "booking number", "id"]:
                    if k in col:
                        bn_idx = col[k]
                        break
                booking_num = cells[bn_idx] if bn_idx is not None and bn_idx < len(cells) else ""

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
                    Court_Date=court_date,
                    Status="In Custody",
                        Release_Date="",
                    Facility=FACILITY,
                    Detail_URL=ROSTER_URL,

                    LastCheckedMode="INITIAL",
                ))

            if records:
                return records

        # Fallback: look for any list of names
        if not records:
            for li in soup.find_all(["li", "p"]):
                text = li.get_text(strip=True)
                # Pattern: "LASTNAME, FIRSTNAME" or "FIRSTNAME LASTNAME"
                if re.match(r"^[A-Z][A-Z\s,\'-]{4,}$", text):
                    full_name = text
                    key = full_name
                    if key in seen:
                        continue
                    seen.add(key)
                    f, m, l = self._parse_name(full_name)
                    records.append(ArrestRecord(
                        County=self.county,
                        Booking_Number="",
                        Full_Name=full_name,
                        First_Name=f, Middle_Name=m, Last_Name=l,
                        DOB="",
                        Status="In Custody",
                        Release_Date="",
                        Facility=FACILITY,
                        Detail_URL=ROSTER_URL,

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
