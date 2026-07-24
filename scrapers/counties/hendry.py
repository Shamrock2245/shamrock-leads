"""
Hendry County Arrest Scraper — Official OCV S3 inmates.json (primary)
======================================================================
Source: Hendry County Sheriff's Office via MyOCV CMS
URL: https://myocv.s3.amazonaws.com/ocvapps/a102933935/inmates.json
Method: HTTP GET → JSON parse → ArrestRecord

HISTORY:
  - v1: OCV SPA HTML enrichment (unreliable)
  - v2: JailTracker Blazor WASM + CAPTCHA (broken)
  - v3: BustedNewspaper RSS (blocked/aborted from VPS 2026-07)
  - v4 (current): Official OCV S3 JSON feed (286+ current inmates)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from curl_cffi import requests as cffi_requests
from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

OCV_INMATES_URL = "https://myocv.s3.amazonaws.com/ocvapps/a102933935/inmates.json"
FACILITY = "Hendry County Jail"
AGENCY = "Hendry County Sheriff's Office"
COUNTY = "Hendry"
IMPERSONATE = "chrome131"

class HendryCountyScraper(BaseScraper):
    """Hendry County — official OCV inmates.json (no CAPTCHA)."""

    @property
    def county(self) -> str:
        return COUNTY

    def scrape(self) -> List[ArrestRecord]:
        logger.info("📡 %s: Fetching official OCV inmates.json...", self.county)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }
        resp = cffi_requests.get(OCV_INMATES_URL, headers=headers, timeout=45, impersonate=IMPERSONATE)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            logger.warning("⚠️ %s: unexpected JSON type %s", self.county, type(data))
            return []

        records: List[ArrestRecord] = []
        seen: set = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                rec = self._parse_inmate(item)
                if not rec:
                    continue
                key = rec.get_dedup_key()
                if key in seen:
                    continue
                seen.add(key)
                records.append(rec)
            except Exception as e:
                logger.warning("⚠️ %s: skip inmate: %s", self.county, e)

        logger.info("✅ %s: Parsed %s records from OCV JSON", self.county, len(records))
        return records

    def _parse_inmate(self, item: Dict[str, Any]) -> Optional[ArrestRecord]:
        inmate_id = str(item.get("inmateID") or "").strip()
        last = str(item.get("lastName") or "").strip()
        first = str(item.get("firstName") or "").strip()
        title = str(item.get("title") or "").strip()

        if title and "," in title:
            full_name = title.upper()
            # title is often "LAST, FIRST MIDDLE"
            parts = [p.strip() for p in title.split(",", 1)]
            last_name = parts[0].upper() if parts else last.upper()
            rest = parts[1].split() if len(parts) > 1 else []
            first_name = (rest[0] if rest else first).upper()
            middle_name = " ".join(rest[1:]).upper() if len(rest) > 1 else ""
        else:
            last_name = last.upper()
            first_name = first.upper()
            middle_name = ""
            full_name = f"{last_name}, {first_name}".strip(", ")

        if not full_name or full_name == ",":
            return None

        booking_number = inmate_id or self._fallback_booking(full_name, item)
        if not booking_number:
            return None

        content = str(item.get("content") or "")
        demographics = self._parse_content_html(content)

        booking_date, booking_time = self._parse_booked_date(
            demographics.get("booked") or content, item.get("date")
        )

        race = (demographics.get("race") or "").strip().upper()[:20]
        sex_raw = (demographics.get("gender") or demographics.get("sex") or "").strip()
        sex = sex_raw[0].upper() if sex_raw else ""
        age = (demographics.get("age") or "").strip()
        height = self._normalize_height(demographics.get("height") or "")
        weight = re.sub(
            r"\s*lbs?\s*", "", demographics.get("weight") or "", flags=re.I
        ).strip()
        if "unavailable" in weight.lower():
            weight = ""

        charges, total_bond = self._parse_charges(item.get("chargeArray"), content)
        status_cd = str(item.get("custody_status_cd") or demographics.get("custody") or "IN")
        status = "In Custody" if status_cd.upper() in ("IN", "ACTIVE", "") else status_cd

        mug = ""
        images = item.get("images") or []
        if isinstance(images, list) and images:
            img0 = images[0] if isinstance(images[0], dict) else {}
            large = str(img0.get("large") or img0.get("small") or "")
            if large and "missing-image" not in large:
                mug = large

        return ArrestRecord(
            County=COUNTY,
            Booking_Number=booking_number,
            Full_Name=full_name,
            First_Name=first_name,
            Middle_Name=middle_name,
            Last_Name=last_name,
            Booking_Date=booking_date,
            Booking_Time=booking_time,
            Arrest_Date=booking_date,
            Age_At_Arrest=age,
            Race=race,
            Sex=sex,
            Height=height,
            Weight=weight,
            Mugshot_URL=mug,
            Charges=charges,
            Bond_Amount=str(total_bond) if total_bond > 0 else "0",
            Status=status,
            Facility=FACILITY,
            Agency=AGENCY,
            Detail_URL="https://www.hendrysheriff.org/inmateSearch",
            Person_ID=inmate_id,
            Scrape_Timestamp=datetime.now(timezone.utc).isoformat(),
            LastChecked=datetime.now(timezone.utc).isoformat(),
            LastCheckedMode="scrape",
            extra_data={
                "source": "ocv_s3_inmates_json",
                "agencyID": item.get("agencyID"),
                "siteID": item.get("siteID"),
            },
        )

    def _parse_content_html(self, html: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if not html:
            return out
        patterns = {
            "height": r"Height:\s*([^<\n]+)",
            "weight": r"Weight:\s*([^<\n]+)",
            "gender": r"Gender:\s*([^<\n]+)",
            "race": r"Race:\s*([^<\n]+)",
            "age": r"Age:\s*([^<\n]+)",
            "custody": r"Custody Status:\s*([^<\n]+)",
            "booked": r"Booked Date:\s*([^<\n]+)",
            "inmate_id": r"Inmate ID:\s*([^<\n]+)",
        }
        for key, pat in patterns.items():
            m = re.search(pat, html, re.I)
            if m:
                val = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                if val and "unavailable" not in val.lower():
                    out[key] = val
        return out

    def _parse_booked_date(
        self, booked_str: str, date_obj: Any
    ) -> Tuple[str, str]:
        # "07/10/2026 10:10:22 EDT"
        if booked_str:
            m = re.search(
                r"(\d{1,2}/\d{1,2}/\d{4})(?:\s+(\d{1,2}:\d{2}:\d{2}))?",
                booked_str,
            )
            if m:
                try:
                    d = datetime.strptime(m.group(1), "%m/%d/%Y")
                    t = m.group(2) or ""
                    return d.strftime("%Y-%m-%d"), t
                except Exception:
                    pass
        if isinstance(date_obj, dict) and "sec" in date_obj:
            try:
                dt = datetime.fromtimestamp(int(date_obj["sec"]), tz=timezone.utc)
                return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
            except Exception:
                pass
        return "", ""

    def _parse_charges(
        self, charge_array: Any, content: str
    ) -> Tuple[str, float]:
        charges: List[str] = []
        total = 0.0
        # Real charge rows are list of dicts; schema-only is list of strings
        if isinstance(charge_array, list) and charge_array:
            if isinstance(charge_array[0], dict):
                for ch in charge_array:
                    if not isinstance(ch, dict):
                        continue
                    desc = (
                        ch.get("chargeDescription")
                        or ch.get("description")
                        or ch.get("charge")
                        or ""
                    )
                    code = ch.get("chargeCode") or ch.get("code") or ""
                    bond = ch.get("bondAmount") or ch.get("bond") or 0
                    label = f"{code} — {desc}".strip(" —") if code else str(desc)
                    if label:
                        charges.append(label)
                    try:
                        total += float(
                            str(bond).replace("$", "").replace(",", "") or 0
                        )
                    except Exception:
                        pass
        if not charges and content:
            # Fallback: free-text charge mentions
            for m in re.finditer(r"Charge[s]?:\s*([^<\n]+)", content, re.I):
                charges.append(m.group(1).strip())
        return " | ".join(charges), total

    def _normalize_height(self, raw: str) -> str:
        if not raw:
            return ""
        m = re.search(r"(\d)\s*ft\s*(\d{1,2})", raw, re.I)
        if m:
            return f"{m.group(1)}{int(m.group(2)):02d}"
        return re.sub(r"[^\d]", "", raw)[:3]

    def _fallback_booking(self, full_name: str, item: Dict[str, Any]) -> str:
        oid = ""
        _id = item.get("_id")
        if isinstance(_id, dict):
            oid = str(_id.get("$id") or "")
        elif _id:
            oid = str(_id)
        base = oid or re.sub(r"[^A-Z0-9]", "", full_name.upper())[:16]
        return f"HENDRY-{base}"
