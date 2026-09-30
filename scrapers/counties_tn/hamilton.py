"""
Hamilton County (TN) Arrest Scraper — Chattanooga / Hamilton County Sheriff.

Portals:
  Daily Bookings:  https://www.hcsheriff.gov/Corrections/Booking-app
                   POST https://www.hcsheriff.gov/Corrections/api/ with {"date": "YYYY-MM-DD"}
  Active Roster:   https://www.hcsheriff.gov/Corrections/Inmates-app
                   GET  https://www.hcsheriff.gov/Corrections/Inmates-app/Full-List/api
  Inmate Detail:   POST https://www.hcsheriff.gov/Corrections/Inmates-app/api with {"type": "data", "info": "<spn>"}

Source Identifiers:
  Booking_Number: Official county booking record GUID (R_ID)
  Person_ID:      Official county System Person Number (SPN)
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

import requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

BOOKING_API = "https://www.hcsheriff.gov/Corrections/api/"
ROSTER_API = "https://www.hcsheriff.gov/Corrections/Inmates-app/Full-List/api"
DETAIL_API = "https://www.hcsheriff.gov/Corrections/Inmates-app/api"
PORTAL_URL = "https://www.hcsheriff.gov/Corrections/Booking-app"
FACILITY = "Hamilton County Jail & Detention Center"
DEFAULT_AGENCY = "Hamilton County Sheriff's Office"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

LOOKBACK_DAYS = 3
DETAIL_PAUSE_S = 0.1
MAX_DETAIL_FETCHES = 100


class HamiltonScraper(BaseScraper):
    """Hamilton County (TN) arrest scraper interfacing with official HCSO APIs."""

    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = ""

    @property
    def county(self) -> str:
        return "Hamilton"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def scraper_id(self) -> str:
        return "scraper_tn_hamilton"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(HEADERS)
        session.verify = True

        records: List[ArrestRecord] = []
        seen_booking_ids: Set[str] = set()

        # ── Phase 1: Fetch active population roster to map SPN and in-custody status ──
        roster_map = self._fetch_roster_map(session)
        logger.info(f"Hamilton (TN): loaded active roster with {len(roster_map)} inmates")

        # ── Phase 2: Fetch recent daily booking reports ──
        today = datetime.now()
        dates_to_query = [
            (today - timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(LOOKBACK_DAYS)
        ]

        raw_bookings: List[Dict[str, Any]] = []
        for d_str in dates_to_query:
            try:
                resp = session.post(
                    BOOKING_API,
                    json={"date": d_str},
                    timeout=20,
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    body = payload.get("body", [])
                    if isinstance(body, list):
                        raw_bookings.extend(body)
                else:
                    logger.warning(f"Hamilton bookings API HTTP {resp.status_code} for {d_str}")
            except Exception as e:
                logger.warning(f"Hamilton bookings fetch error for {d_str}: {e}")

        logger.info(f"Hamilton (TN): fetched {len(raw_bookings)} booking records across {len(dates_to_query)} days")

        # ── Phase 3: Normalize and enrich records ──
        details_fetched = 0
        for b in raw_bookings:
            r_id = b.get("R_ID")
            if not r_id or r_id in seen_booking_ids:
                continue
            seen_booking_ids.add(r_id)

            name_raw = str(b.get("Name") or "").strip()
            if not name_raw:
                continue

            first, last = self._split_name(name_raw)

            # Charges
            charges: List[str] = []
            for i in range(1, 49):
                off = b.get(f"PrtOffense{i}")
                if off and str(off).strip():
                    charges.append(str(off).strip())
            charge_str = "; ".join(charges) if charges else "Unknown"

            # Committal date/time
            comm_date_raw = str(b.get("HML_COMMITTAL_DATE") or "")
            comm_date = ""
            if comm_date_raw:
                comm_date = self._parse_iso_date(comm_date_raw)
            comm_time = str(b.get("HML_COMMITTAL_TIME") or "").strip()

            # Age and agency
            age = str(b.get("HML_AGE_AT_ARREST") or "").strip()
            agency = str(b.get("HML_ARREST_AGENCY") or "").strip() or DEFAULT_AGENCY

            # Cross-reference with active in-custody roster
            name_key = f"{last.upper()},{first.upper()}"
            inm = roster_map.get(name_key)

            spn = ""
            dob = ""
            bond = "0"
            court_date = ""
            status = "Released"

            if inm:
                status = "In Custody"
                spn = str(inm.get("spn") or "").strip()
                dob_raw = str(inm.get("dob") or "").strip()
                if dob_raw:
                    dob = self._parse_iso_date(dob_raw)

                # Fetch bond & court date if under rate limit
                if spn and details_fetched < MAX_DETAIL_FETCHES:
                    detail = self._fetch_detail(session, spn)
                    if detail:
                        if detail.get("bond"):
                            bond = detail["bond"]
                        if detail.get("court_date"):
                            court_date = detail["court_date"]
                    details_fetched += 1
                    time.sleep(DETAIL_PAUSE_S)

            rec = self._booking_to_record(
                b,
                roster_bond=bond if bond != "0" else None,
                detail_info={"dob": dob, "bond": bond, "court_date": court_date, "status": status} if inm else None,
                spn_override=spn if spn else None,
            )
            if rec:
                records.append(rec)

        elapsed = time.time() - start
        logger.info(
            f"✅ Hamilton (TN): {len(records)} records "
            f"({details_fetched} details enriched) in {elapsed:.1f}s"
        )
        return records

    def _booking_to_record(
        self,
        b: Dict[str, Any],
        roster_bond: Optional[str] = None,
        detail_info: Optional[Dict[str, str]] = None,
        spn_override: Optional[str] = None,
    ) -> Optional[ArrestRecord]:
        r_id = b.get("R_ID")
        name_raw = str(b.get("Name") or b.get("FullName") or "").strip()
        if not r_id or not name_raw:
            return None

        first, last = self._split_name(name_raw)

        charges: List[str] = []
        if b.get("Charges"):
            charges.append(str(b["Charges"]))
        for i in range(1, 49):
            off = b.get(f"PrtOffense{i}")
            if off and str(off).strip():
                charges.append(str(off).strip())
        charge_str = "; ".join(charges) if charges else "Unknown"

        comm_date_raw = str(b.get("HML_COMMITTAL_DATE") or b.get("BookingDate") or "")
        comm_date = ""
        if comm_date_raw:
            comm_date = self._parse_iso_date(comm_date_raw)
        comm_time = str(b.get("HML_COMMITTAL_TIME") or "").strip()

        age = str(b.get("HML_AGE_AT_ARREST") or b.get("Age") or "").strip()
        agency = str(b.get("HML_ARREST_AGENCY") or b.get("ArrestingAgency") or "").strip() or DEFAULT_AGENCY

        spn = spn_override or str(b.get("SPN") or "").strip()
        dob = ""
        bond = roster_bond or "0"
        court_date = ""
        facility = FACILITY
        status = "Released"

        if detail_info:
            if detail_info.get("bond"):
                bond = detail_info["bond"]
            if detail_info.get("court_date"):
                court_date = detail_info["court_date"]
            if detail_info.get("dob"):
                dob = detail_info["dob"]
            if detail_info.get("facility"):
                facility = detail_info["facility"]
            if detail_info.get("status"):
                status = detail_info["status"]
            else:
                status = "In Custody"
        elif roster_bond:
            status = "In Custody"

        return ArrestRecord(
            County=self.county,
            State=self.state,
            Full_Name=name_raw.title() if name_raw.isupper() else name_raw,
            First_Name=first.title(),
            Last_Name=last.title(),
            Booking_Number=str(r_id),
            Person_ID=str(spn or r_id),
            DOB=dob,
            Age_At_Arrest=age,
            Charges=charge_str,
            Bond_Amount=bond,
            Booking_Date=comm_date,
            Booking_Time=comm_time,
            Arrest_Date=comm_date,
            Status=status,
            Court_Date=court_date,
            Facility=facility,
            Agency=agency,
            Detail_URL=f"https://www.hcsheriff.gov/Corrections/Inmates-app/{spn}" if spn else PORTAL_URL,
            extra_data={
                "r_id": r_id,
                "spn": spn,
                "address_street": b.get("AddressStreet"),
                "address_city": b.get("AddressCity"),
                "address_zip": b.get("AddressZip"),
            },
            LastCheckedMode="INITIAL",
        )

    # ── API Methods ──────────────────────────────────────────────────────────

    def _fetch_roster_map(self, session: requests.Session) -> Dict[str, Dict[str, Any]]:
        """Fetch active population roster and index by LAST,FIRST."""
        out: Dict[str, Dict[str, Any]] = {}
        try:
            resp = session.get(ROSTER_API, timeout=30)
            if resp.status_code == 200:
                roster = resp.json()
                if isinstance(roster, dict):
                    for letter, inmates in roster.items():
                        if isinstance(inmates, list):
                            for inm in inmates:
                                last = (inm.get("last_name") or "").strip().upper()
                                first = (inm.get("first_name") or "").strip().upper()
                                if last and first:
                                    out[f"{last},{first}"] = inm
        except Exception as e:
            logger.warning(f"Hamilton active roster fetch error: {e}")
        return out

    def _fetch_detail(self, session: requests.Session, spn: str) -> Optional[Dict[str, str]]:
        """Fetch bond and court date detail by SPN."""
        try:
            resp = session.post(
                DETAIL_API,
                json={"type": "data", "info": spn},
                timeout=10,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None

            out: Dict[str, str] = {}
            bond_raw = str(data.get("bond_amount") or "0")
            bond_clean = re.sub(r"[^\d.]", "", bond_raw) or "0"
            out["bond"] = bond_clean

            court_raw = str(data.get("court_date") or "")
            if court_raw and not court_raw.startswith("1900"):
                out["court_date"] = self._parse_iso_date(court_raw)

            return out
        except Exception as e:
            logger.debug(f"Hamilton detail fetch error {spn}: {e}")
            return None

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_iso_date(raw: str) -> str:
        """Parse ISO date string to MM/DD/YYYY format."""
        if not raw or raw == "None":
            return ""
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
        if m:
            y, mo, d = m.groups()
            return f"{mo}/{d}/{y}"
        return raw.strip()

    @staticmethod
    def _split_name(name_raw: str) -> tuple[str, str]:
        """Split 'LAST, FIRST MIDDLE' into (first, last)."""
        if not name_raw:
            return ("", "")
        if "," in name_raw:
            parts = [p.strip() for p in name_raw.split(",", 1)]
            last = parts[0]
            first_rest = parts[1] if len(parts) > 1 else ""
            first = first_rest.split()[0] if first_rest else ""
            return (first, last)
        parts = name_raw.split()
        if len(parts) == 1:
            return (parts[0], "")
        return (parts[0], parts[-1])
