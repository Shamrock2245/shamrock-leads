"""
Lake County Arrest Scraper — JSON API
Source: Lake County Sheriff's Office
URL: https://www.lcso.org/inmate-search/api/inmates
Method: curl_cffi POST — clean JSON REST API, no reCAPTCHA (token validation disabled server-side)
Fields: firstname, lastname, dob, booking_number, facility + charges
Updated: 2026-05-18 — replaced DrissionPage with direct JSON API
"""
import logging
import re
from datetime import datetime, timedelta
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.lcso.org"
API_URL = f"{BASE_URL}/inmate-search/api/inmates"
FACILITY = "Lake County Jail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": "https://www.lcso.org/inmate-search/",
    "Origin": "https://www.lcso.org",
}


class LakeCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Lake"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cf
        except ImportError:
            logger.error("curl_cffi not installed")
            raise

        session = cf.Session()
        records: List[ArrestRecord] = []
        seen: set = set()

        # Search with empty name to get all recent inmates
        # The API accepts: firstname, lastname, dob, booking_number, facility, token
        payload = {
            "firstname": "",
            "lastname": "",
            "dob": "",
            "booking_number": "",
            "facility": "",
            "token": "bypass",  # reCAPTCHA is disabled server-side
        }

        try:
            r = session.post(
                API_URL,
                json=payload,
                headers=HEADERS,
                timeout=20,
                impersonate="chrome131",
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.error(f"Lake API error: {e}")
            raise

        inmates = data if isinstance(data, list) else data.get("records", data.get("inmates", data.get("data", [])))
        if not inmates:
            logger.warning(f"Lake: no inmates in response. Keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
            return records

        cutoff = datetime.now() - timedelta(days=7)

        for inmate in inmates:
            if not isinstance(inmate, dict):
                continue

            booking_number = str(inmate.get("booking_number") or inmate.get("bookingNumber") or inmate.get("id") or "")
            if not booking_number or booking_number in seen:
                continue
            seen.add(booking_number)

            first = (inmate.get("firstname") or inmate.get("firstName") or "").strip().title()
            last = (inmate.get("lastname") or inmate.get("lastName") or "").strip().title()
            middle = (inmate.get("middlename") or inmate.get("middleName") or "").strip().title()
            dob_raw = inmate.get("dob") or inmate.get("dateOfBirth") or ""
            booking_date_raw = inmate.get("booking_date") or inmate.get("bookingDate") or ""
            facility = inmate.get("facility") or FACILITY
            gender = (inmate.get("gender") or inmate.get("sex") or "").strip()
            race = (inmate.get("race") or "").strip()
            address = (inmate.get("address") or "").strip()
            city = (inmate.get("city") or "").strip()
            state = (inmate.get("state") or "FL").strip()
            zip_code = (inmate.get("zip") or "").strip()

            # Parse charges
            charges_raw = inmate.get("charges") or inmate.get("offenses") or []
            if isinstance(charges_raw, list):
                charges = "; ".join(
                    str(c.get("description") or c.get("charge") or c) for c in charges_raw if c
                )
            else:
                charges = str(charges_raw)

            bond_raw = inmate.get("bond_amount") or inmate.get("bondAmount") or inmate.get("total_bond") or "0"
            bond_amount = re.sub(r"[$,\s]", "", str(bond_raw))

            # Filter by booking date if available
            if booking_date_raw:
                try:
                    bd = datetime.fromisoformat(str(booking_date_raw).replace("Z", ""))
                    if bd < cutoff:
                        continue
                except Exception:
                    pass

            full_address = ", ".join(filter(None, [address, city, state, zip_code]))

            record = ArrestRecord(
                County="Lake",
                Booking_Number=booking_number,
                Full_Name=f"{first} {middle} {last}".strip(),
                First_Name=first,
                Middle_Name=middle,
                Last_Name=last,
                DOB=dob_raw,
                Booking_Date=booking_date_raw,
                Status="In Custody",
                Release_Date="",
                Facility=facility,
                Sex=gender,
                Race=race,
                Address=full_address,
                Charges=charges,
                Bond_Amount=bond_amount,
                Detail_URL=f"{BASE_URL}/inmate-search/",
                LastCheckedMode="INITIAL",
            )
            records.append(record)

        logger.info(f"Lake: {len(records)} records")
        return records
