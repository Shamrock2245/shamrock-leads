"""
Monroe County Arrest Scraper — Keys SO data API (v2)
====================================================
Source: Monroe County Sheriff's Office (Keys SO)
URL: https://data.keysso.net/api/arrests
Method: HTTP GET → JSON (last ~7 days of arrest logs) → ArrestRecord
Fields: Name, Arrest Date/Time, Charges, Bond, Mugshot, Arraignment

HISTORY:
  - v1: ASP.NET disclaimer POST + name search on www.keysso.net/arrestQintro
        (dead 2026-07 — site rebuilt as SvelteKit SPA, "Disclaimer POST 403")
  - v2 (current): official JSON feed consumed by the new SPA
        (`/arrests` page → GET https://data.keysso.net/api/arrests).
        No captcha, no proxy, no disclaimer. Returns ArrestLog1..8.json
        buckets, one per day, ~80 records/week.

Natural key: the MNI id embedded in the mugshot filename
(e.g. MCSO78MNI183475) + arrest date; falls back to name+date hash.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional

import requests
from curl_cffi import requests as cffi_requests
import urllib3

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

API_URL = "https://data.keysso.net/api/arrests"
PUBLIC_PAGE = "https://www.keysso.net/arrests"
FACILITY = "Monroe County Detention Center"
AGENCY = "Monroe County Sheriff's Office"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.keysso.net",
    "Referer": "https://www.keysso.net/",
}

_MNI_RE = re.compile(r"ArrestLogs/([A-Z0-9]+?)L?\.(?:jpg|jpeg|png)", re.I)

# ── Stealth Stack ──────────────────────────────────────────────────────────────
IMPERSONATE = "chrome131"
STEALTH_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
}

class MonroeCountyScraper(BaseScraper):
    """Monroe County (FL) — Keys SO official arrest-log JSON API."""

    @property
    def county(self) -> str:
        return "Monroe"

    def scrape(self) -> List[ArrestRecord]:
        logger.info("📡 %s: Fetching Keys SO arrest API...", self.county)
        # keysso.net serves an incomplete intermediate chain (same as v1) —
        # verify=False is required; suppress the noisy warning locally.
        with urllib3.warnings.catch_warnings():
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            resp = cffi_requests.get(API_URL, headers=HEADERS, timeout=45, verify=False, impersonate=IMPERSONATE)
        resp.raise_for_status()
        data = resp.json()

        if not isinstance(data, list):
            logger.warning("⚠️ %s: unexpected JSON type %s", self.county, type(data))
            return []

        records: List[ArrestRecord] = []
        seen: set = set()
        for bucket in data:
            if not isinstance(bucket, dict):
                continue
            arrests = (bucket.get("data") or {}).get("arrests") or []
            for item in arrests:
                if not isinstance(item, dict):
                    continue
                try:
                    rec = self._parse_arrest(item)
                except Exception as e:  # defensive: one bad row must not kill the run
                    logger.warning("⚠️ %s: skip row: %s", self.county, e)
                    continue
                if not rec:
                    continue
                key = rec.get_dedup_key()
                if key in seen:
                    continue
                seen.add(key)
                records.append(rec)

        logger.info("✅ %s: Parsed %s records from Keys SO API", self.county, len(records))
        return records

    # ── parsing ────────────────────────────────────────────────────────────

    def _parse_arrest(self, item: Dict[str, Any]) -> Optional[ArrestRecord]:
        full_name = " ".join(str(item.get("Name") or "").split())
        if not full_name or len(full_name) < 3:
            return None

        arrest_date = str(item.get("ArrestDate") or "").strip()
        arrest_time = str(item.get("ArrestTime") or "").strip()

        booking_num = self._natural_key(item, full_name, arrest_date)
        first, middle, last = self._parse_name(full_name)

        charges = "; ".join(
            str(c.get("Charge") or "").strip()
            for c in (item.get("Charges") or [])
            if isinstance(c, dict) and c.get("Charge")
        )

        dob = str(item.get("DoB") or "").strip()
        if dob in ("", "NA", "N/A"):
            dob = ""
        age = str(item.get("Age") or "").strip()
        if age in ("", "NA", "N/A"):
            age = ""
        address = str(item.get("Address") or "").strip()
        if address.lower() in ("not available", "unknown"):
            address = ""

        court_date, court_time = self._parse_arraignment(
            str(item.get("Arraignment") or "")
        )

        return ArrestRecord(
            County=self.county,
            Booking_Number=booking_num,
            Full_Name=full_name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            DOB=dob,
            Age_At_Arrest=age,
            Arrest_Date=arrest_date,
            Arrest_Time=arrest_time,
            Booking_Date=arrest_date,
            Status="In Custody",
            Facility=FACILITY,
            Agency=AGENCY,
            Race=str(item.get("Race") or "").strip(),
            Sex=str(item.get("Sex") or "").strip(),
            Address=address,
            Mugshot_URL=str(item.get("mugShot") or "").strip(),
            Charges=charges,
            Bond_Amount=str(self._parse_bond(item.get("Bond"))),
            Court_Date=court_date,
            Court_Time=court_time,
            Detail_URL=PUBLIC_PAGE,
            LastCheckedMode="INITIAL",
        )

    @staticmethod
    def _natural_key(item: Dict[str, Any], full_name: str, arrest_date: str) -> str:
        """Stable id: MNI from mugshot filename, else offense/CAD no, else hash."""
        for field in ("mugShot", "mugShotL"):
            m = _MNI_RE.search(str(item.get(field) or ""))
            if m:
                return m.group(1)
        for field in ("OffenseNo", "CADno"):
            v = str(item.get(field) or "").strip()
            if v:
                return f"MONROE-{v}"
        digest = hashlib.sha1(f"{full_name}|{arrest_date}".encode()).hexdigest()[:12]
        return f"MONROE-{digest.upper()}"

    @staticmethod
    def _parse_arraignment(raw: str) -> tuple:
        """'08/12/2026 at 09:00' → ('08/12/2026', '09:00')."""
        raw = raw.strip()
        if not raw:
            return "", ""
        m = re.match(r"(\d{2}/\d{2}/\d{4})(?:\s+at\s+(\d{1,2}:\d{2}))?", raw)
        if not m:
            return "", ""
        return m.group(1), m.group(2) or ""

    @staticmethod
    def _parse_name(name: str):
        if not name:
            return "", "", ""
        name = " ".join(name.strip().split())
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip()
            fm = parts[1].strip().split()
            first = fm[0] if fm else ""
            middle = " ".join(fm[1:]) if len(fm) > 1 else ""
            return first, middle, last
        parts = name.split()
        if len(parts) == 1:
            return parts[0], "", ""
        if len(parts) == 2:
            return parts[0], "", parts[1]
        return parts[0], " ".join(parts[1:-1]), parts[-1]

    @staticmethod
    def _parse_bond(bond_val) -> float:
        if bond_val is None:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", str(bond_val).strip().upper())
        if not cleaned or any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
