"""
Travis County (TX) Arrest Scraper — SIPS Public Inmate API.

Portal: https://public.co.travis.tx.us/sips/
API:    https://public.traviscountytx.gov/sip/api/v2/inmates
Travis County is the 5th-largest TX county (~1.3M pop) encompassing Austin.
Uses stealth stack (make_stealth_request, curl_cffi) to query the SIPS REST API
which returns JSON inmate lists and detailed charge/bond information.
"""
from __future__ import annotations

import logging
import time
from typing import List, Set, Tuple

from scrapers.base_scraper import BaseScraper
from scrapers.stealth_utils import make_stealth_request
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

API_BASE = "https://public.traviscountytx.gov/sip/api/v2"
LIST_URL = f"{API_BASE}/inmates"
DETAIL_URL = f"{API_BASE}/inmates"  # + /{bookingNumber}
PORTAL_URL = "https://public.co.travis.tx.us/sips/"

# Walk A-Z last-name prefixes for comprehensive coverage
LAST_NAME_PREFIXES = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


class TravisScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Travis"

    @property
    def state(self) -> str:
        return "TX"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        seen_booking: Set[str] = set()

        # Step 1: Collect all inmate summaries via A-Z last-name walk
        summaries = self._collect_summaries(seen_booking)

        # Step 2: Fetch detail for each inmate to get charges/bond
        for summary in summaries:
            booking_number = summary.get("bookingNumber", "")
            if not booking_number:
                continue

            detail = self._fetch_detail(booking_number)
            rec = self._build_record(summary, detail)
            if rec:
                records.append(rec)

        logger.info(
            f"✅ Travis (TX): {len(records)} records "
            f"({len(summaries)} summaries fetched) "
            f"in {time.time() - start:.1f}s"
        )
        return records

    def _collect_summaries(self, seen_booking: Set[str]) -> List[dict]:
        """Walk A-Z last-name prefixes on the list endpoint."""
        out: List[dict] = []

        for prefix in LAST_NAME_PREFIXES:
            try:
                resp = make_stealth_request(
                    f"{LIST_URL}?lastName={prefix}",
                    method="GET",
                    timeout=20,
                )
                if not resp or resp.status_code != 200:
                    continue

                batch = resp.json()
                if not isinstance(batch, list):
                    continue

                for item in batch:
                    bk = str(item.get("bookingNumber", "")).strip()
                    if not bk or bk in seen_booking:
                        continue
                    seen_booking.add(bk)
                    out.append(item)

            except Exception as e:
                logger.debug(f"Travis list prefix={prefix} failed: {e}")

        return out

    def _fetch_detail(self, booking_number: str) -> dict:
        """Fetch full detail (charges, bond, facility) for a booking."""
        try:
            resp = make_stealth_request(
                f"{DETAIL_URL}/{booking_number}",
                method="GET",
                timeout=15,
            )
            if resp and resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug(f"Travis detail booking={booking_number} failed: {e}")
        return {}

    def _build_record(self, summary: dict, detail: dict) -> ArrestRecord | None:
        """Construct an ArrestRecord from summary + detail data."""
        full_name = (summary.get("fullName") or "").strip()
        if not full_name or len(full_name) < 2:
            return None

        booking_number = str(summary.get("bookingNumber", "")).strip()
        age = summary.get("age")

        # Detail fields
        facility = (detail.get("facility") or "Travis County Correctional Complex").strip()
        agency = (detail.get("arrestingAgency") or "Travis County Sheriff's Office").strip()
        booking_date = (detail.get("bookingDate") or "").strip()

        # Parse charges and bond from detail
        charges_list = detail.get("charges", [])
        charge_texts = []
        total_bond = 0
        bond_type = ""
        for ch in charges_list:
            ct = (ch.get("chargeText") or "").strip()
            if ct:
                charge_texts.append(ct)
            ba = ch.get("bondAmount") or 0
            if isinstance(ba, (int, float)):
                total_bond += int(ba)
            bt = ch.get("bondType") or ""
            if bt and not bond_type:
                bond_type = bt

        charges = "; ".join(charge_texts) if charge_texts else "Unknown"
        first_name, last_name = self._split_name(full_name)

        # Normalize booking date (ISO → MM/DD/YYYY)
        arrest_date = ""
        if booking_date and "T" in booking_date:
            date_part = booking_date.split("T")[0]
            parts = date_part.split("-")
            if len(parts) == 3:
                arrest_date = f"{parts[1]}/{parts[2]}/{parts[0]}"

        return ArrestRecord(
            County=self.county,
            State=self.state,
            Booking_Number=f"TRAVIS_{booking_number}",
            Person_ID=str(detail.get("id", "")),
            Full_Name=full_name,
            First_Name=first_name,
            Last_Name=last_name,
            Age_At_Arrest=str(age) if age else "",
            Charges=charges,
            Bond_Amount=str(total_bond),
            Bond_Type=bond_type,
            Status="In Custody",
            Facility=facility,
            Agency=agency,
            Booking_Date=arrest_date,
            Arrest_Date=arrest_date,
            Detail_URL=f"{PORTAL_URL}#/sips-detail/{booking_number}",
        )

    @staticmethod
    def _split_name(name: str) -> Tuple[str, str]:
        """Split 'Last, First Middle' into (first, last)."""
        name = name.strip()
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip().title()
            first = parts[1].strip().title()
            return first, last
        bits = name.split()
        if len(bits) >= 2:
            return bits[0].title(), bits[-1].title()
        return name.title(), ""
