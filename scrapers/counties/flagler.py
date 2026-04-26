"""
Flagler County Arrest Scraper — New World InmateInquiry
Source: Flagler County Sheriff's Office
URL: https://nwwebcad.fcpsn.org/NewWorld.InmateInquiry/FL0180000
Method: requests GET — New World SPA, JSON API via XHR
Fields: Name, Subject Number, Race, Gender, DOB, Height, Weight, Booking Date, Status
"""

import logging
import re
import time
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://nwwebcad.fcpsn.org/NewWorld.InmateInquiry/FL0180000"
API_URL = "https://nwwebcad.fcpsn.org/NewWorld.InmateInquiry/FL0180000/api/Inmates"
FACILITY = "Flagler County Jail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_URL,
    "X-Requested-With": "XMLHttpRequest",
}


class FlaglerCountyScraper(BaseScraper):
    """Flagler County (FL) — New World InmateInquiry"""

    @property
    def county(self) -> str:
        return "Flagler"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed")
            return []

        records = []

        # Try JSON API first (New World SPA pattern)
        try:
            resp = requests.get(
                API_URL,
                headers=HEADERS,
                params={"inCustody": "true", "pageSize": 500, "pageNumber": 1},
                timeout=30,
            )
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        records = self._parse_json(data)
                        logger.info(f"Flagler JSON API: {len(records)} records")
                        return records
                    elif isinstance(data, dict) and data.get("inmates"):
                        records = self._parse_json(data["inmates"])
                        logger.info(f"Flagler JSON API: {len(records)} records")
                        return records
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Flagler JSON API failed: {e}")

        # Fallback: HTML scrape
        try:
            resp = requests.get(BASE_URL, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            records = self._parse_html(soup)
            logger.info(f"Flagler HTML: {len(records)} records")
        except Exception as e:
            logger.error(f"Flagler HTML fallback failed: {e}")

        return records

    def _parse_json(self, inmates: list) -> List[ArrestRecord]:
        records = []
        seen = set()
        for inmate in inmates:
            try:
                full_name = inmate.get("name", inmate.get("fullName", "")).strip()
                if not full_name:
                    continue
                booking_num = str(inmate.get("bookingNumber", inmate.get("subjectNumber", ""))).strip()
                key = booking_num or full_name
                if key in seen:
                    continue
                seen.add(key)

                f, m, l = self._parse_name(full_name)
                dob = inmate.get("dateOfBirth", inmate.get("dob", ""))
                booking_date = inmate.get("bookingDate", inmate.get("arrestDate", ""))
                status = inmate.get("custodyStatus", inmate.get("status", "In Custody"))
                race = inmate.get("race", "")
                sex = inmate.get("sex", inmate.get("gender", ""))
                height = inmate.get("height", "")
                weight = inmate.get("weight", "")
                charges_raw = inmate.get("charges", inmate.get("chargeDescription", ""))
                if isinstance(charges_raw, list):
                    charges_raw = " | ".join(str(c) for c in charges_raw)
                bond_raw = inmate.get("bondAmount", inmate.get("bond", "0"))

                records.append(ArrestRecord(
                    County=self.county,
                    Booking_Number=booking_num,
                    Full_Name=full_name,
                    First_Name=f, Middle_Name=m, Last_Name=l,
                    DOB=str(dob) if dob else "",
                    Booking_Date=str(booking_date) if booking_date else "",
                    Status=self._normalize_status(str(status)),
                    Facility=FACILITY,
                    Race=str(race) if race else "",
                    Sex=str(sex) if sex else "",
                    Height=str(height) if height else "",
                    Weight=str(weight) if weight else "",
                    Charges=str(charges_raw) if charges_raw else "",
                    Bond_Amount=str(self._parse_bond(str(bond_raw))),
                    LastCheckedMode="INITIAL",
                ))
            except Exception as e:
                logger.debug(f"Flagler parse error: {e}")
        return records

    def _parse_html(self, soup) -> List[ArrestRecord]:
        records = []
        seen = set()
        # New World renders a table with inmate rows
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])]
            if not any(h in headers for h in ["name", "inmate", "subject"]):
                continue
            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if not cells or not cells[0]:
                    continue
                full_name = cells[0]
                booking_num = cells[1] if len(cells) > 1 else ""
                key = booking_num or full_name
                if key in seen:
                    continue
                seen.add(key)
                f, m, l = self._parse_name(full_name)
                booking_date = cells[2] if len(cells) > 2 else ""
                dob = cells[3] if len(cells) > 3 else ""
                race = cells[4] if len(cells) > 4 else ""
                sex = cells[5] if len(cells) > 5 else ""
                records.append(ArrestRecord(
                    County=self.county,
                    Booking_Number=booking_num,
                    Full_Name=full_name,
                    First_Name=f, Middle_Name=m, Last_Name=l,
                    DOB=dob,
                    Booking_Date=booking_date,
                    Status="In Custody",
                    Facility=FACILITY,
                    Race=race,
                    Sex=sex,
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
    def _normalize_status(status: str) -> str:
        s = status.lower()
        if any(x in s for x in ["custody", "confined", "held", "in jail", "active"]):
            return "In Custody"
        if any(x in s for x in ["released", "bonded", "rts"]):
            return "Released"
        return status or "In Custody"

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
