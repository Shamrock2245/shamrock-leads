"""
Craven County (NC) Arrest Scraper — ArcGIS BookingsPublic MapServer.

Portal UI: https://gis.cravencountync.gov/images/activebookings/
API:       https://gis.cravencountync.gov/arcgis/rest/services/BookingsPublic/MapServer/0/query

Charge-level features are aggregated by BookingNumber (bond, statutes, descriptions).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List

import requests

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

QUERY_URL = (
    "https://gis.cravencountync.gov/arcgis/rest/services/"
    "BookingsPublic/MapServer/0/query"
)
PORTAL_URL = "https://gis.cravencountync.gov/images/activebookings/"
OUT_FIELDS = (
    "Facility,BookingORI,BookingNumber,LName,MName,FName,Age,FullAddress,"
    "City,State,Zip,BookingDatetime,BondTotal,Statute,Description,"
    "BondCourtdateTime,DateOfBirth,InmateNumber"
)


class CravenScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Craven"

    @property
    def state(self) -> str:
        return "NC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        })

        try:
            resp = session.get(
                QUERY_URL,
                params={
                    "where": "1=1",
                    "outFields": OUT_FIELDS,
                    "returnGeometry": "false",
                    "f": "json",
                    "orderByFields": "LName,FName",
                    "resultRecordCount": 10000,
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("Craven ArcGIS query failed: %s", e)
            return []

        features = data.get("features") or []
        if not features:
            logger.warning("Craven: zero features from MapServer")
            return []

        # Aggregate charge-level rows → one record per booking
        by_booking: Dict[str, dict] = {}
        for feat in features:
            a = feat.get("attributes") or {}
            booking = str(a.get("BookingNumber") or "").strip()
            if not booking:
                continue
            last = (a.get("LName") or "").strip()
            first = (a.get("FName") or "").strip()
            middle = (a.get("MName") or "").strip()
            if not last and not first:
                continue

            charge = (a.get("Description") or "").strip()
            statute = (a.get("Statute") or "").strip()
            charge_bits = [x for x in (statute, charge) if x]
            charge_str = " ".join(charge_bits) if charge_bits else ""

            bond_val = a.get("BondTotal")
            try:
                bond_f = float(bond_val) if bond_val is not None else 0.0
            except (TypeError, ValueError):
                bond_f = 0.0

            if booking not in by_booking:
                full = f"{last}, {first} {middle}".strip().replace("  ", " ").strip(", ")
                by_booking[booking] = {
                    "booking": booking,
                    "last": last,
                    "first": first,
                    "middle": middle,
                    "full": full,
                    "age": a.get("Age") or "",
                    "dob": a.get("DateOfBirth") or "",
                    "address": a.get("FullAddress") or "",
                    "city": a.get("City") or "",
                    "zip": a.get("Zip") or "",
                    "facility": a.get("Facility") or "Craven County Detention",
                    "admit": a.get("BookingDatetime") or "",
                    "court": a.get("BondCourtdateTime") or "",
                    "inmate_number": a.get("InmateNumber") or "",
                    "charges": [],
                    "bond": bond_f,
                }
            rec = by_booking[booking]
            if charge_str and charge_str not in rec["charges"]:
                rec["charges"].append(charge_str)
            if bond_f > rec["bond"]:
                rec["bond"] = bond_f

        records = [self._to_record(v) for v in by_booking.values()]
        logger.info(
            "Craven: %d inmates from %d charge rows in %.1fs",
            len(records),
            len(features),
            time.time() - start,
        )
        return records

    def _to_record(self, row: dict) -> ArrestRecord:
        charges = " | ".join(row["charges"]) if row["charges"] else "Unknown"
        bond = row["bond"]
        bond_str = f"{bond:.2f}" if bond else "0"
        booking = str(row["booking"])
        # Prefer Jacket/Inmate number when present for Person_ID
        person = str(row.get("inmate_number") or booking)

        return ArrestRecord(
            County=self.county,
            State="NC",
            Full_Name=row["full"],
            First_Name=row["first"],
            Middle_Name=row["middle"],
            Last_Name=row["last"],
            Booking_Number=booking,
            Person_ID=person,
            Age_At_Arrest=str(row.get("age") or ""),
            DOB=str(row.get("dob") or ""),
            Booking_Date=str(row.get("admit") or ""),
            Court_Date=str(row.get("court") or ""),
            Address=row.get("address") or "",
            City=row.get("city") or "",
            ZIP=str(row.get("zip") or ""),
            Charges=charges,
            Bond_Amount=re.sub(r"[^\d.]", "", bond_str) or "0",
            Status="In Custody",
            Facility=row.get("facility") or "Craven County Detention",
            Detail_URL=PORTAL_URL,
            Agency="Craven County Sheriff",
        )
