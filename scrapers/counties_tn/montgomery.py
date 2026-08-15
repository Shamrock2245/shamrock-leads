"""
Montgomery County (TN) Arrest Scraper — Clarksville MCSO Current Inmates.

Portal embeds JSON inmate list (~600 active):
  https://mcsojail.countygovservices.com/Home/CurrentInmates

Public inquiry also at:
  https://api.mcgtn.org/publicinquiry/inmateroster/search
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import List

import requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

ROSTER_URL = "https://mcsojail.countygovservices.com/Home/CurrentInmates"
FACILITY = "Montgomery County Jail"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class MontgomeryScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "The configured Montgomery public paths did not establish a complete "
        "booking-safe broad listing through ordinary access."
    )

    @property
    def county(self) -> str:
        return "Montgomery"

    @property
    def state(self) -> str:
        return "TN"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        try:
            resp = requests.get(ROSTER_URL, headers=HEADERS, timeout=45)
            resp.raise_for_status()
            inmates = self._extract_json(resp.text)
            for row in inmates:
                rec = self._to_record(row)
                if rec:
                    records.append(rec)
        except Exception as e:
            logger.error(f"Montgomery (TN) scrape failed: {e}")

        logger.info(f"✅ Montgomery (TN): {len(records)} records in {time.time() - start:.1f}s")
        return records

    @staticmethod
    def _extract_json(html: str) -> list:
        idx = html.find('[{"BookNum"')
        if idx < 0:
            # alternate key order
            m = re.search(r'(\[\{"[A-Za-z]+":', html)
            if not m:
                return []
            idx = m.start()
        decoder = json.JSONDecoder()
        try:
            data, _ = decoder.raw_decode(html[idx:])
            return data if isinstance(data, list) else []
        except json.JSONDecodeError as e:
            logger.warning(f"Montgomery JSON decode: {e}")
            return []

    def _to_record(self, row: dict) -> ArrestRecord | None:
        if not isinstance(row, dict):
            return None
        booking = str(row.get("BookNum") or "").strip()
        last = (row.get("NameLast") or "").strip()
        first = (row.get("NameFirst") or "").strip()
        middle = (row.get("NameMiddle") or "").strip()
        full = (row.get("FullName") or "").strip()
        if not full:
            parts = [last, first, middle]
            full = ", ".join(p for p in [last, " ".join(x for x in [first, middle] if x)] if p)
        if not booking and not full:
            return None
        if not booking:
            booking = f"MONT_{hash(full) & 0xFFFFFFFF:08x}"

        book_date = (row.get("BookDate") or "")[:10]
        if "T" in str(row.get("BookDate") or ""):
            book_date = str(row["BookDate"]).split("T")[0]
        dob = (row.get("DOB") or "")[:10]
        if "T" in str(row.get("DOB") or ""):
            dob = str(row["DOB"]).split("T")[0]

        charges = row.get("Charges")
        if isinstance(charges, list):
            charge_str = "; ".join(str(c) for c in charges if c)
        elif charges:
            charge_str = str(charges)
        else:
            charge_str = "Unknown"

        return ArrestRecord(
            County=self.county,
            State="TN",
            Full_Name=full,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=booking,
            Booking_Date=book_date,
            DOB=dob,
            Charges=charge_str,
            Bond_Amount="0",
            Status="In Custody",
            Facility=FACILITY,
            Detail_URL=ROSTER_URL,
            LastCheckedMode="INITIAL",
        )
