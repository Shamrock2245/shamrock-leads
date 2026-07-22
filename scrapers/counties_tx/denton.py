"""
Denton County (TX) Arrest Scraper — Denton Police Athena JailView API.

Portal: https://athena.dentonpolice.com/JailView/
API:    POST JailView.aspx/GetInmates (returns JSON array of current inmates)
Denton County is the 7th-largest TX county (~1.0M pop) in the DFW metro.
Uses stealth stack (make_stealth_request, curl_cffi) to query the Athena
JailView WebMethod which returns all current city jail inmates with charges,
bond amounts, and booking details.

Note: This covers Denton City Police jail. The county-level Tyler/Odyssey
PublicAccess system (justice1.dentoncounty.gov) is currently returning errors
and will be added as a secondary source when it stabilizes.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import List, Set, Tuple

from scrapers.base_scraper import BaseScraper
from scrapers.stealth_utils import make_stealth_request
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

JAILVIEW_URL = "https://athena.dentonpolice.com/JailView/JailView.aspx/GetInmates"
PORTAL_URL = "https://athena.dentonpolice.com/JailView/"


class DentonScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Denton"

    @property
    def state(self) -> str:
        return "TX"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        seen: Set[str] = set()

        try:
            inmates = self._fetch_inmates()
            for inmate in inmates:
                rec = self._build_record(inmate, seen)
                if rec:
                    records.append(rec)
        except Exception as e:
            logger.error(f"Denton scrape failed: {e}")

        logger.info(
            f"✅ Denton (TX): {len(records)} records in {time.time() - start:.1f}s"
        )
        return records

    def _fetch_inmates(self) -> List[dict]:
        """Call GetInmates WebMethod and parse the JSON response."""
        resp = make_stealth_request(
            JAILVIEW_URL,
            method="POST",
            json={},
            timeout=30,
        )
        if not resp or resp.status_code != 200:
            logger.warning(
                f"Denton GetInmates returned {resp.status_code if resp else 'None'}"
            )
            return []

        data = resp.json()

        # Response format: {"d": "<JSON array as string>"}
        raw = data.get("d", "")
        if not raw:
            return []

        try:
            inmates = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Denton JSON parse error: {e}")
            return []

        if not isinstance(inmates, list):
            return []

        return inmates

    def _build_record(self, inmate: dict, seen: Set[str]) -> ArrestRecord | None:
        """Convert a single Athena JailView inmate dict to ArrestRecord."""
        name = (inmate.get("name") or "").strip()
        if not name or len(name) < 2:
            return None

        book_no = (inmate.get("bookno") or "").strip()
        book_handle = (inmate.get("bookhandle") or "").strip()

        # Dedup key: booking number or handle
        dedup_key = book_no or book_handle
        if not dedup_key:
            return None

        booking_id = f"DEN_{dedup_key}"
        if booking_id in seen:
            return None
        seen.add(booking_id)

        # Parse fields
        charges = (inmate.get("charges") or "").strip()
        datetime_booked = (inmate.get("datetimebooked") or "").strip()
        bonds_raw = (inmate.get("outstandingbonds") or "").strip()
        amount_raw = (inmate.get("amount") or "").strip()
        detainers = (inmate.get("detainers") or "").strip()

        # Parse bond amount (format: "$2,500.00")
        bond_amount = self._parse_currency(amount_raw or bonds_raw)

        # Parse booking date (format: "MM/DD/YYYY HH:MM")
        arrest_date = ""
        if datetime_booked:
            date_part = datetime_booked.split(" ")[0] if " " in datetime_booked else datetime_booked
            arrest_date = date_part

        # Combine charges and detainers
        full_charges = charges
        if detainers:
            full_charges = f"{charges}; DETAINER: {detainers}" if charges else f"DETAINER: {detainers}"

        first_name, last_name = self._split_name(name)

        return ArrestRecord(
            County=self.county,
            State=self.state,
            Booking_Number=booking_id,
            Person_ID=book_handle,
            Full_Name=name.title() if name.isupper() else name,
            First_Name=first_name,
            Last_Name=last_name,
            Charges=full_charges or "Unknown",
            Bond_Amount=str(bond_amount),
            Status="In Custody",
            Facility="Denton City Jail",
            Agency="Denton Police Department",
            Booking_Date=arrest_date,
            Arrest_Date=arrest_date,
            Detail_URL=PORTAL_URL,
        )

    @staticmethod
    def _parse_currency(raw: str) -> int:
        """Parse '$2,500.00' → 2500 (integer dollars)."""
        if not raw:
            return 0
        cleaned = re.sub(r"[^0-9.]", "", raw)
        try:
            return int(float(cleaned))
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _split_name(name: str) -> Tuple[str, str]:
        """Split 'LAST, FIRST MIDDLE' into (first, last)."""
        name = name.replace("\xa0", " ").strip()
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip().title()
            first = parts[1].strip().title()
            return first, last
        bits = name.split()
        if len(bits) >= 2:
            return bits[0].title(), bits[-1].title()
        return name.title(), ""
