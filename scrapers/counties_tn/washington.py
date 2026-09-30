"""
Washington County (TN) Arrest Scraper — Jonesborough / Washington County Sheriff.

Source:
  Booking Sheet PDF: https://www.wcso.net/arrests/Website_Booking_Sheet.pdf
  Intake Frame:     https://www.wcso.net/arrests/intake.php

The Washington County Sheriff's Office publishes a daily 30-day rolling booking
sheet PDF containing all recent bookings, charges, demographics, and official
county booking numbers. This scraper extracts the structured text records directly
from the public PDF using PyMuPDF (fitz).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import List, Optional, Set, Tuple

import fitz  # PyMuPDF
import requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

PDF_URL = "https://www.wcso.net/arrests/Website_Booking_Sheet.pdf"
FACILITY = "Washington County Detention Center"
DEFAULT_AGENCY = "Washington County Sheriff's Office"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*",
}

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2}$")
BOOKING_RE = re.compile(r"^\d{5,10}$")
HEADER_RE = re.compile(r"^(Page \d+ of \d+|Website Booking Sheet|Printed on .*)$")


class WashingtonScraper(BaseScraper):
    """Scrape the public booking sheet for Washington County, Tennessee."""

    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = ""

    @property
    def county(self) -> str:
        return "Washington"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def scraper_id(self) -> str:
        return "scraper_tn_washington"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        seen: Set[str] = set()

        try:
            logger.info("Washington (TN): downloading booking sheet from %s", PDF_URL)
            resp = requests.get(PDF_URL, headers=HEADERS, timeout=45)
            resp.raise_for_status()

            doc = fitz.open(stream=resp.content, filetype="pdf")
            logger.info("Washington (TN): opened PDF with %d pages", len(doc))

            parsed_entries = self._extract_entries_from_pdf(doc)
            logger.info("Washington (TN): extracted %d raw records from PDF", len(parsed_entries))

            for entry in parsed_entries:
                rec = self._entry_to_record(entry)
                if rec:
                    key = rec.get_dedup_key()
                    if key not in seen:
                        seen.add(key)
                        records.append(rec)

        except requests.RequestException as exc:
            logger.error("Washington (TN) PDF download failed: %s", exc)
        except Exception as exc:
            logger.error("Washington (TN) PDF parse failed: %s", exc)

        logger.info("Washington (TN): %d records in %.1fs", len(records), time.time() - start)
        return records

    @classmethod
    def _extract_entries_from_pdf(cls, doc: fitz.Document) -> List[dict]:
        """Extract structured entry dictionaries from the multi-page PDF."""
        all_lines: List[str] = []
        for p in range(len(doc)):
            page_text = doc[p].get_text()
            for raw in page_text.splitlines():
                line = raw.strip()
                if not line or HEADER_RE.match(line):
                    continue
                all_lines.append(line)

        entries: List[dict] = []
        i = 0
        n = len(all_lines)
        while i < n:
            line = all_lines[i]
            if BOOKING_RE.match(line):
                booking_num = line
                if i + 4 < n:
                    name = all_lines[i + 1]
                    sex = all_lines[i + 2]
                    race = all_lines[i + 3]
                    age_str = all_lines[i + 4]
                    if sex in ("Male", "Female", "Unknown") and age_str.isdigit():
                        charges: List[str] = []
                        j = i + 5
                        date_str = ""
                        while j < n and not BOOKING_RE.match(all_lines[j]):
                            if DATE_RE.match(all_lines[j]):
                                date_str = all_lines[j]
                                j += 1
                                break
                            charges.append(all_lines[j])
                            j += 1
                        entries.append({
                            "booking_number": booking_num,
                            "name": name,
                            "sex": sex,
                            "race": race,
                            "age": age_str,
                            "charges": "; ".join(charges) if charges else "Unknown",
                            "booking_date": date_str,
                        })
                        i = j
                        continue
            i += 1

        return entries

    def _entry_to_record(self, entry: dict) -> Optional[ArrestRecord]:
        raw_name = entry.get("name", "").strip()
        booking_number = entry.get("booking_number", "").strip()
        if not raw_name or not booking_number:
            return None

        first, middle, last = self._parse_name(raw_name)
        full_name = raw_name.title() if raw_name.isupper() else raw_name
        booking_date = self._format_date(entry.get("booking_date", ""))

        return ArrestRecord(
            County=self.county,
            State=self.state,
            Full_Name=full_name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=booking_number,
            Booking_Date=booking_date,
            Arrest_Date=booking_date,
            Age_At_Arrest=entry.get("age", ""),
            Race=entry.get("race", ""),
            Sex=entry.get("sex", ""),
            Charges=entry.get("charges", "Unknown"),
            Bond_Amount="0",
            Status="In Custody",
            Facility=FACILITY,
            Agency=DEFAULT_AGENCY,
            Detail_URL=PDF_URL,
            LastCheckedMode="INITIAL",
        )

    @staticmethod
    def _parse_name(raw_name: str) -> Tuple[str, str, str]:
        """Parse 'LAST, FIRST MIDDLE [SUFFIX]' into (first, middle, last)."""
        if "," in raw_name:
            parts = [p.strip() for p in raw_name.split(",", 1)]
            last = parts[0]
            remainder = parts[1] if len(parts) > 1 else ""
            rem_parts = remainder.split()
            first = rem_parts[0] if rem_parts else ""
            middle = " ".join(rem_parts[1:]) if len(rem_parts) > 1 else ""
        else:
            parts = raw_name.split()
            first = parts[0] if parts else ""
            last = parts[-1] if len(parts) > 1 else ""
            middle = " ".join(parts[1:-1]) if len(parts) > 2 else ""

        first = first.title() if first.isupper() else first
        middle = middle.title() if middle.isupper() else middle
        last = last.title() if last.isupper() else last
        return first, middle, last

    @staticmethod
    def _format_date(raw_date: str) -> str:
        """Convert '09/30/26 02:16' to '09/30/2026 02:16'."""
        if not raw_date:
            return ""
        try:
            dt = datetime.strptime(raw_date.strip(), "%m/%d/%y %H:%M")
            return dt.strftime("%m/%d/%Y %H:%M")
        except ValueError:
            return raw_date
