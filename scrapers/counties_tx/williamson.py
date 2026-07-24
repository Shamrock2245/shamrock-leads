"""
Williamson County (TX) Arrest Scraper.

Portal: https://www.wilco.org/Sheriff/Inmate-Search or Jail View
Williamson County is a major Austin Metro county (~640k pop).

Dedup key: Booking_Number (SO / Booking ID)
"""
from __future__ import annotations

import logging
import time
from typing import List, Tuple
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord
from scrapers.stealth_utils import make_stealth_request

logger = logging.getLogger(__name__)

PORTAL_URL = "https://www.wilco.org/Sheriff/Inmate-Search"
SEARCH_URL = "https://inmates.wilco.org/JailView/GetInmates"

LETTER_WALK = ["A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "R", "S", "T", "W"]


class WilliamsonScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Williamson"

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
                logger.warning(f"Williamson letter {letter} failed: {e}")

        logger.info(f"✅ Williamson (TX): {len(records)} records in {time.time() - start:.1f}s")
        return records

    def _scrape_letter(self, letter: str, seen: set) -> List[ArrestRecord]:
        payload = {"lastName": letter, "firstName": ""}
        resp = make_stealth_request(
            SEARCH_URL,
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
            items = data.get("d") or data.get("inmates") or data if isinstance(data, list) else []
            if isinstance(items, str):
                import json
                items = json.loads(items)

            for item in items:
                booking = str(item.get("BookingNumber") or item.get("InmateId") or item.get("soNumber", "")).strip()
                if not booking or booking in seen:
                    continue
                seen.add(booking)

                name = item.get("FullName") or f"{item.get('LastName', '')}, {item.get('FirstName', '')}".strip(", ")
                first = item.get("FirstName", "")
                last = item.get("LastName", "")
                dob = item.get("DOB") or item.get("DateOfBirth", "")
                charges = item.get("Charges") or item.get("Offenses", "Unknown")
                bond = str(item.get("BondAmount") or item.get("TotalBond", "")).strip()

                out.append(
                    ArrestRecord(
                        County=self.county,
                        State="TX",
                        Full_Name=name.title() if name.isupper() else name,
                        First_Name=first.title() if first else "",
                        Last_Name=last.title() if last else "",
                        Booking_Number=booking,
                        DOB=str(dob),
                        Charges=str(charges),
                        Bond_Amount=self._clean_bond(bond),
                        Status="In Custody",
                        Facility="Williamson County Jail",
                        Agency="Williamson County Sheriff",
                        Detail_URL=PORTAL_URL,
                    )
                )
        except Exception as e:
            logger.debug(f"Williamson parse error: {e}")

        return out

    @staticmethod
    def _clean_bond(val: str) -> str:
        if not val or val == "0":
            return ""
        clean = "".join(c for c in str(val) if c.isdigit())
        return clean if clean and clean != "0" else ""
