"""
Montgomery County (TX) Arrest Scraper.

Portal: https://mctxsheriff.org/inmate_inquiry/
Montgomery County is a major North Houston metro county (~650k pop).

Dedup key: Booking_Number (SPN / Booking ID)
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

PORTAL_URL = "https://mctxsheriff.org/inmate_inquiry/"
SEARCH_URL = "https://mctxsheriff.org/inmate_inquiry/search.php"

LETTER_WALK = ["A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "R", "S", "T", "W"]


class MontgomeryScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Montgomery"

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
                logger.warning(f"Montgomery letter {letter} failed: {e}")

        logger.info(f"✅ Montgomery (TX): {len(records)} records in {time.time() - start:.1f}s")
        return records

    def _scrape_letter(self, letter: str, seen: set) -> List[ArrestRecord]:
        params = {"lname": letter, "fname": "", "submit": "Search"}
        resp = make_stealth_request(SEARCH_URL, method="GET", params=params, timeout=25)

        out: List[ArrestRecord] = []
        if not resp or resp.status_code != 200:
            return out

        soup = BeautifulSoup(resp.text, "html.parser")
        tables = soup.find_all("table")
        if not tables:
            return out

        table = max(tables, key=lambda t: len(t.find_all("tr")))
        rows = table.find_all("tr")

        for row in rows[1:]:
            cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
            if len(cells) < 4:
                continue

            booking = cells[0].strip()
            name = cells[1].strip()
            dob = cells[2].strip() if len(cells) > 2 else ""
            charges = cells[3].strip() if len(cells) > 3 else "Unknown"
            bond = cells[4].strip() if len(cells) > 4 else ""

            if not booking or booking in seen or not name:
                continue
            seen.add(booking)

            first, last = self._split_name(name)

            out.append(
                ArrestRecord(
                    County=self.county,
                    State="TX",
                    Full_Name=name.title() if name.isupper() else name,
                    First_Name=first,
                    Last_Name=last,
                    Booking_Number=booking,
                    DOB=dob,
                    Charges=charges,
                    Bond_Amount=self._clean_bond(bond),
                    Status="In Custody",
                    Facility="Montgomery County Jail",
                    Agency="Montgomery County Sheriff",
                    Detail_URL=PORTAL_URL,
                )
            )

        return out

    @staticmethod
    def _clean_bond(val: str) -> str:
        if not val or val == "0":
            return ""
        clean = "".join(c for c in str(val) if c.isdigit())
        return clean if clean and clean != "0" else ""

    @staticmethod
    def _split_name(name: str) -> Tuple[str, str]:
        if "," in name:
            parts = name.split(",", 1)
            return parts[1].strip().title(), parts[0].strip().title()
        parts = name.split()
        if len(parts) >= 2:
            return parts[0].title(), parts[-1].title()
        return name.title(), ""
