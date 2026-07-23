"""
Monroe County Arrest Scraper — Keys SO Custom ASP.NET
Source: Monroe County Sheriff's Office (Keys SO)
URL: https://www.keysso.net/arrestQintro
Method: requests POST — disclaimer acceptance + name search
Fields: Name, Booking Date, Charges, Bond Amount, Status
"""

import logging
import re
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

INTRO_URL = "https://www.keysso.net/arrestQintro"
SEARCH_URL = "https://www.keysso.net/arrestQ"
FACILITY = "Monroe County Detention Center"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": INTRO_URL,
    "DNT": "1",
    "Connection": "keep-alive",
}
IMPERSONATE = "chrome131"


class MonroeCountyScraper(BaseScraper):
    """Monroe County (FL) — Keys SO arrest query (Key West area)"""

    @property
    def county(self) -> str:
        return "Monroe"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cffi_requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("curl_cffi/bs4 not installed")
            raise

        session = cffi_requests.Session()
        # keysso.net serves an incomplete intermediate chain — same pattern as SmartCOP bases.
        req_kw = {"timeout": 30, "impersonate": IMPERSONATE, "verify": False}

        # Step 1: GET intro page (disclaimer)
        try:
            resp = session.get(INTRO_URL, headers=HEADERS, **req_kw)
            if resp.status_code != 200:
                raise Exception(f"GET intro {resp.status_code}")
        except Exception as e:
            logger.error(f"Monroe intro GET failed: {e}")
            raise

        soup = BeautifulSoup(resp.text, "html.parser")
        viewstate = soup.find("input", {"name": "__VIEWSTATE"})
        viewstate_gen = soup.find("input", {"name": "__VIEWSTATEGENERATOR"})
        event_val = soup.find("input", {"name": "__EVENTVALIDATION"})

        # Step 2: Accept disclaimer
        disclaimer_data = {
            "__VIEWSTATE": viewstate["value"] if viewstate else "",
            "__VIEWSTATEGENERATOR": viewstate_gen["value"] if viewstate_gen else "",
            "__EVENTVALIDATION": event_val["value"] if event_val else "",
            "btnAgree": "I Agree",
        }

        try:
            resp = session.post(INTRO_URL, data=disclaimer_data, headers=HEADERS, **req_kw)
            if resp.status_code not in (200, 302):
                raise Exception(f"Disclaimer POST {resp.status_code}")
        except Exception as e:
            logger.error(f"Monroe disclaimer POST failed: {e}")
            raise

        # Step 3: Search with blank last name (all current inmates)
        soup2 = BeautifulSoup(resp.text, "html.parser")
        viewstate2 = soup2.find("input", {"name": "__VIEWSTATE"})
        viewstate_gen2 = soup2.find("input", {"name": "__VIEWSTATEGENERATOR"})
        event_val2 = soup2.find("input", {"name": "__EVENTVALIDATION"})

        search_data = {
            "__VIEWSTATE": viewstate2["value"] if viewstate2 else "",
            "__VIEWSTATEGENERATOR": viewstate_gen2["value"] if viewstate_gen2 else "",
            "__EVENTVALIDATION": event_val2["value"] if event_val2 else "",
            "txtLastName": "",
            "txtFirstName": "",
            "btnSearch": "Search",
        }

        try:
            resp = session.post(
                SEARCH_URL if SEARCH_URL != INTRO_URL else resp.url,
                data=search_data,
                headers={**HEADERS, "Referer": resp.url},
                timeout=60,
                impersonate=IMPERSONATE,
                verify=False,
            )
            if resp.status_code != 200:
                raise Exception(f"Search POST {resp.status_code}")
        except Exception as e:
            logger.error(f"Monroe search POST failed: {e}")
            raise

        records = self._parse(resp.text)
        logger.info(f"Monroe: {len(records)} records")
        return records

    def _parse(self, html: str) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        records = []
        seen = set()

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_text = rows[0].get_text(" ").lower()
            if not any(k in header_text for k in ["name", "inmate", "booking", "arrest"]):
                continue
            headers = [th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])]
            col = {h: i for i, h in enumerate(headers)}

            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if not cells:
                    continue
                name_idx = col.get("name", col.get("inmate name", 0))
                full_name = cells[name_idx] if name_idx < len(cells) else cells[0]
                if not full_name or len(full_name) < 3:
                    continue

                bd_idx = None
                for k in ["booking date", "booking", "arrest date", "date"]:
                    if k in col:
                        bd_idx = col[k]
                        break
                booking_date = cells[bd_idx] if bd_idx is not None and bd_idx < len(cells) else ""

                bn_idx = None
                for k in ["booking #", "booking no", "booking number", "id"]:
                    if k in col:
                        bn_idx = col[k]
                        break
                booking_num = cells[bn_idx] if bn_idx is not None and bn_idx < len(cells) else ""

                bond_idx = None
                for k in ["bond", "bond amount", "bail"]:
                    if k in col:
                        bond_idx = col[k]
                        break
                bond_raw = cells[bond_idx] if bond_idx is not None and bond_idx < len(cells) else "0"

                charge_idx = None
                for k in ["charge", "charges", "offense"]:
                    if k in col:
                        charge_idx = col[k]
                        break
                charges = cells[charge_idx] if charge_idx is not None and charge_idx < len(cells) else ""

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
                    Status="In Custody",
                        Release_Date="",
                    Facility=FACILITY,
                    Charges=charges,
                    Bond_Amount=str(self._parse_bond(bond_raw)),
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
