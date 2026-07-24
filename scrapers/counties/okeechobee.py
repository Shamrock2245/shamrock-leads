"""
Okeechobee County Arrest Scraper — Custom HTML Table
Source: Okeechobee County Sheriff's Office
URL: https://www.okeesheriff.org/inmate-search
Method: requests GET — direct HTML table of current inmates
Fields: Name, Booking Date, Status, Charges, Bond Amount
"""

import logging
import re
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

from curl_cffi import requests as cffi_requests
logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.okeesheriff.org/inmate-search"
FACILITY = "Okeechobee County Jail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.okeesheriff.org/",
}

class OkeechobeeCountyScraper(BaseScraper):
    """Okeechobee County (FL) — Custom HTML inmate table"""

    @property
    def county(self) -> str:
        return "Okeechobee"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed")
            raise

        try:
            resp = cffi_requests.get(ROSTER_URL, headers=HEADERS, timeout=30, impersonate=IMPERSONATE)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Okeechobee: fetch failed: {e}")
            raise

        soup = BeautifulSoup(resp.text, "html.parser")

        # Try JSON embedded in page
        records = self._try_parse_json_in_page(soup)
        if records:
            return records

        records = self._parse_html(soup)
        if not records:
            raise RuntimeError("Okeechobee: No records parsed from JSON or HTML")

        logger.info(f"Okeechobee HTML: {len(records)} records")
        return records

    def _try_parse_json_in_page(self, soup) -> List[ArrestRecord]:
        try:
            import json
            scripts = soup.find_all("script")
            for script in scripts:
                text = script.string or ""
                if "inmates" in text.lower() or "bookingNumber" in text or "booking_number" in text:
                    # Try to extract JSON array
                    match = re.search(r'\[(\{[^;]+\})\]', text, re.DOTALL)
                    if match:
                        try:
                            data = json.loads("[" + match.group(1) + "]")
                            if data:
                                records = self._parse_json(data)
                                if records:
                                    logger.info(f"Okeechobee JSON-in-page: {len(records)} records")
                                    return records
                        except Exception:
                            pass
        except Exception:
            pass
        return []

    def _parse_json(self, inmates: list) -> List[ArrestRecord]:
        records = []
        seen = set()
        for inmate in inmates:
            try:
                full_name = inmate.get("name", inmate.get("fullName", "")).strip()
                if not full_name:
                    continue
                booking_num = str(inmate.get("bookingNumber", inmate.get("id", ""))).strip()
                key = booking_num or full_name
                if key in seen:
                    continue
                seen.add(key)
                f, m, l = self._parse_name(full_name)
                dob = inmate.get("dateOfBirth", inmate.get("dob", ""))
                booking_date = inmate.get("bookingDate", "")
                status = inmate.get("custodyStatus", inmate.get("status", "In Custody"))
                race = inmate.get("race", "")
                sex = inmate.get("sex", inmate.get("gender", ""))
                charges_raw = inmate.get("charges", "")
                if isinstance(charges_raw, list):
                    charges_raw = " | ".join(str(c) for c in charges_raw)
                bond_raw = inmate.get("bondAmount", "0")
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
                    Charges=str(charges_raw) if charges_raw else "",
                    Bond_Amount=str(self._parse_bond(str(bond_raw))),
                    Detail_URL=ROSTER_URL,

                    LastCheckedMode="INITIAL",
                ))
            except Exception as e:
                logger.debug(f"Okeechobee JSON parse error: {e}")
        return records

    def _parse_html(self, soup) -> List[ArrestRecord]:
        records = []
        seen = set()

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
                name_idx = col.get("name", col.get("inmate name", 0))
                full_name = cells[name_idx] if name_idx < len(cells) else cells[0]
                if not full_name or len(full_name) < 3:
                    continue

                bd_idx = None
                for k in ["booking date", "booking", "date"]:
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
                    Booking_Date=booking_date,
                    Status="In Custody",
                        Release_Date="",
                    Facility=FACILITY,
                    Charges=charges,
                    Bond_Amount=str(self._parse_bond(bond_raw)),
                    Detail_URL=ROSTER_URL,

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
