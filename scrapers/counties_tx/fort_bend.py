"""
Fort Bend County (TX) Arrest Scraper.

Portal: https://pos.fortbendcountytx.gov/ or Sheriff Inmate Search API
Fort Bend is a major Houston Metro county (~860k pop).

Dedup key: Booking_Number (SO Number or Booking ID)
"""
from __future__ import annotations

import logging
import time
from typing import List, Tuple

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord
from scrapers.stealth_utils import make_stealth_request

logger = logging.getLogger(__name__)

PORTAL_URL = "https://pos.fortbendcountytx.gov/inmatesearch"
SEARCH_API = "https://pos.fortbendcountytx.gov/api/inmates/search"

# Common last name letters to walk
LETTER_WALK = ["A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "R", "S", "T", "W"]


class FortBendScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Fort Bend"

    @property
    def state(self) -> str:
        return "TX"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        seen: set = set()

        import datetime
        hour = datetime.datetime.now().hour
        letters = LETTER_WALK[hour % len(LETTER_WALK): (hour % len(LETTER_WALK)) + 2]
        if not letters:
            letters = ["A", "B"]

        for letter in letters:
            try:
                sub_records = self._scrape_letter(letter, seen)
                records.extend(sub_records)
                time.sleep(1.0)
            except Exception as e:
                logger.warning(f"Fort Bend letter {letter} failed: {e}")

        logger.info(f"✅ Fort Bend (TX): {len(records)} records in {time.time() - start:.1f}s")
        return records

    def _scrape_letter(self, letter: str, seen: set) -> List[ArrestRecord]:
        payload = {"lastName": letter, "firstName": "", "status": "In Custody"}
        resp = make_stealth_request(
            SEARCH_API,
            method="POST",
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=25,
        )

        out: List[ArrestRecord] = []
        if not resp or resp.status_code != 200:
            return out

        try:
            data = resp.json()
            items = data.get("inmates", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for item in items:
                booking = str(item.get("bookingNumber") or item.get("soNumber") or item.get("id", "")).strip()
                if not booking or booking in seen:
                    continue
                seen.add(booking)

                full_name = f"{item.get('lastName', '')}, {item.get('firstName', '')}".strip(", ")
                first = item.get("firstName", "")
                last = item.get("lastName", "")
                dob = item.get("dob") or item.get("dateOfBirth", "")
                charges = item.get("charges") or item.get("offense", "Unknown")
                if isinstance(charges, list):
                    charges = "; ".join([str(c.get("description", c)) if isinstance(c, dict) else str(c) for c in charges])

                bond = str(item.get("bondAmount") or item.get("totalBond", "")).strip()

                out.append(
                    ArrestRecord(
                        County=self.county,
                        State="TX",
                        Full_Name=full_name.title() if full_name.isupper() else full_name,
                        First_Name=first.title(),
                        Last_Name=last.title(),
                        Booking_Number=booking,
                        Person_ID=str(item.get("soNumber", booking)),
                        DOB=dob,
                        Charges=str(charges),
                        Bond_Amount=self._clean_bond(bond),
                        Status="In Custody",
                        Facility="Fort Bend County Jail",
                        Agency="Fort Bend County Sheriff",
                        Detail_URL=PORTAL_URL,
                    )
                )
        except Exception as e:
            logger.debug(f"Fort Bend parse error: {e}")

        return out

    @staticmethod
    def _clean_bond(val: str) -> str:
        if not val or val == "0":
            return ""
        clean = "".join(c for c in str(val) if c.isdigit())
        return clean if clean and clean != "0" else ""
