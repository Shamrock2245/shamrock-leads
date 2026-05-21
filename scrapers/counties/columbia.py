"""
Columbia County Arrest Scraper — P2C (Police to Citizen)
Source: Columbia County Sheriff's Office
URL: https://columbiacountyso.policetocitizen.com/Inmates
Method: requests GET — P2C JSON API
Fields: Name, DOB, Status, Booking Number, Booking Date, Age, Bond Amount,
        Address, Statute, Court Case Number, Charge, Degree, Level, Bond
"""

import logging
import re
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://columbiacountyso.policetocitizen.com/Inmates"
API_URL = "https://columbiacountyso.policetocitizen.com/Inmates/GetInmates"
FACILITY = "Columbia County Jail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_URL,
    "X-Requested-With": "XMLHttpRequest",
}


class ColumbiaCountyScraper(BaseScraper):
    """Columbia County (FL) — P2C inmate search (Lake City area)"""

    @property
    def county(self) -> str:
        return "Columbia"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed")
            raise

        # Try P2C JSON API
        for endpoint in [API_URL, BASE_URL + "/GetInmates", BASE_URL]:
            try:
                resp = requests.get(
                    endpoint,
                    headers=HEADERS,
                    params={"page": 1, "pageSize": 500, "inCustody": True},
                    timeout=30,
                )
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        inmates = []
                        if isinstance(data, list):
                            inmates = data
                        elif isinstance(data, dict):
                            inmates = data.get("data", data.get("inmates", data.get("results", [])))
                        if inmates:
                            records = self._parse_json(inmates)
                            logger.info(f"Columbia P2C JSON: {len(records)} records")
                            return records
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Columbia {endpoint} failed: {e}")

        # HTML fallback
        try:
            resp = requests.get(BASE_URL, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            records = self._parse_html(soup)
            logger.info(f"Columbia HTML: {len(records)} records")
            return records
        except Exception as e:
            logger.error(f"Columbia HTML failed: {e}")
            raise

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
                booking_date = inmate.get("bookingDate", inmate.get("arrestDate", ""))
                status = inmate.get("custodyStatus", inmate.get("status", "In Custody"))
                race = inmate.get("race", "")
                sex = inmate.get("sex", inmate.get("gender", ""))
                address = inmate.get("address", inmate.get("addressGiven", ""))

                # Charges
                charges_list = inmate.get("charges", [])
                if isinstance(charges_list, list):
                    charge_strs = []
                    total_bond = 0.0
                    for ch in charges_list:
                        if isinstance(ch, dict):
                            desc = ch.get("charge", ch.get("chargeDescription", ch.get("description", "")))
                            bond = ch.get("bond", ch.get("bondAmount", 0))
                            if desc:
                                charge_strs.append(str(desc))
                            try:
                                total_bond += float(str(bond).replace(",", "").replace("$", ""))
                            except (ValueError, TypeError):
                                pass
                        elif isinstance(ch, str):
                            charge_strs.append(ch)
                    charges_str = " | ".join(charge_strs)
                    bond_amount = total_bond
                else:
                    charges_str = str(charges_list) if charges_list else ""
                    bond_amount = self._parse_bond(str(inmate.get("bondAmount", inmate.get("bond", "0"))))

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
                    Address=str(address) if address else "",
                    Charges=charges_str,
                    Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                    Detail_URL=BASE_URL,

                    LastCheckedMode="INITIAL",
                ))
            except Exception as e:
                logger.debug(f"Columbia parse error: {e}")
        return records

    def _parse_html(self, soup) -> List[ArrestRecord]:
        records = []
        seen = set()
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_text = rows[0].get_text(" ").lower()
            if not any(k in header_text for k in ["name", "inmate", "booking"]):
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
                records.append(ArrestRecord(
                    County=self.county,
                    Booking_Number=booking_num,
                    Full_Name=full_name,
                    First_Name=f, Middle_Name=m, Last_Name=l,
                    Booking_Date=booking_date,
                    Status="In Custody",
                        Release_Date="",
                    Facility=FACILITY,
                    Detail_URL=BASE_URL,

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
