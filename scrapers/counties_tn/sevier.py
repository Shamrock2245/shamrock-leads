"""
Sevier County (TN) Arrest Scraper — Sevier County Sheriff's Office.

Portal: https://www.seviercountysheriff.com/inmateRoster
Official Source Identifier: Numeric Inmate ID (e.g., 974489).
Contract: Name, Inmate ID, Booked Date/Time, Age, Race, Sex, Custody Status.
Charges on this public roster are not published (recorded as "Unknown" with bond "0").
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

PORTAL_URL = "https://www.seviercountysheriff.com/inmateRoster"
FACILITY = "Sevier County Jail"
AGENCY = "Sevier County Sheriff's Office"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MAX_PAGES = 10
REQUEST_PAUSE_S = 0.2


class SevierScraper(BaseScraper):
    """Sevier County (TN) arrest scraper interfacing with official SCSO public roster."""

    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = ""

    @property
    def county(self) -> str:
        return "Sevier"

    @property
    def state(self) -> str:
        return "TN"

    @property
    def scraper_id(self) -> str:
        return "scraper_tn_sevier"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(HEADERS)
        session.verify = True

        records: List[ArrestRecord] = []
        seen: Set[str] = set()

        for page in range(1, MAX_PAGES + 1):
            try:
                time.sleep(REQUEST_PAUSE_S)
                url = f"{PORTAL_URL}?page={page}"
                resp = session.get(url, timeout=25)
                if resp.status_code != 200:
                    logger.warning(f"Sevier page {page} returned HTTP {resp.status_code}")
                    break

                page_entries = self._extract_flight_entries(resp.text)
                if not page_entries:
                    break

                new_count = 0
                for entry in page_entries:
                    rec = self._parse_entry(entry)
                    if not rec:
                        continue
                    if rec.Booking_Number in seen:
                        continue
                    seen.add(rec.Booking_Number)
                    records.append(rec)
                    new_count += 1

                if new_count == 0:
                    break

            except Exception as e:
                logger.warning(f"Sevier scrape error on page {page}: {e}")
                break

        elapsed = time.time() - start
        logger.info(f"✅ Sevier (TN): {len(records)} records in {elapsed:.1f}s")
        return records

    def _extract_flight_entries(self, html: str) -> List[Dict[str, Any]]:
        """Extract JSON entries array from Next.js Flight/RSC script blocks."""
        chunks = re.findall(r'self\.__next_f\.push\(\[1,\s*\"(.*?)\"\]\)', html, re.DOTALL)
        for c in chunks:
            if 'entries' in c:
                try:
                    unescaped = c.encode().decode('unicode-escape')
                    idx = unescaped.find('\"entries\":[')
                    if idx != -1:
                        sub = unescaped[idx + len('\"entries\":'):]
                        decoder = json.JSONDecoder()
                        entries, _ = decoder.raw_decode(sub)
                        if isinstance(entries, list):
                            return entries
                except Exception as e:
                    logger.debug(f"Sevier Flight chunk decode error: {e}")
        return []

    def _parse_entry(self, entry: Dict[str, Any]) -> Optional[ArrestRecord]:
        inmate_id = str(entry.get("inmateID") or "").strip()
        name_raw = str(entry.get("title") or "").strip()
        if not inmate_id or not name_raw:
            return None

        first, last = self._split_name(name_raw)

        # Parse content HTML for demographics and booked date
        content = str(entry.get("content") or "")
        fields = self._parse_content_html(content)

        booked_date = fields.get("booked_date", "")
        age = fields.get("age", "")
        race = fields.get("race", "")
        gender = fields.get("gender", "")

        status_cd = str(entry.get("custody_status_cd") or "").upper()
        status = "In Custody" if status_cd in ("IN", "CUSTODY") else "Released"

        oid = ""
        _id = entry.get("_id")
        if isinstance(_id, dict):
            oid = str(_id.get("$id") or "").strip()
        elif _id:
            oid = str(_id).strip()

        detail_url = f"{PORTAL_URL}/{oid}" if oid else PORTAL_URL

        return ArrestRecord(
            County=self.county,
            State=self.state,
            Full_Name=name_raw,
            First_Name=first.title(),
            Last_Name=last.title(),
            Booking_Number=str(inmate_id),
            Person_ID=str(inmate_id),
            Age_At_Arrest=age,
            Race=race,
            Sex=gender,
            Booking_Date=booked_date,
            Arrest_Date=booked_date,
            Charges="Unknown",
            Bond_Amount="0",
            Status=status,
            Facility=FACILITY,
            Agency=AGENCY,
            Detail_URL=detail_url,
            extra_data={"oid": oid, "site_id": entry.get("siteID")},
        )

    @staticmethod
    def _parse_content_html(html: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if not html:
            return out
        text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)

        m_date = re.search(r"Booked Date:\s*([0-9/]+(?:\s*[0-9:]+)?)", text)
        if m_date:
            out["booked_date"] = m_date.group(1).strip()

        m_age = re.search(r"Age:\s*(\d+)", text)
        if m_age:
            out["age"] = m_age.group(1).strip()

        m_race = re.search(r"Race:\s*([A-Za-z]+)", text)
        if m_race:
            out["race"] = m_race.group(1).strip()

        m_gen = re.search(r"Gender:\s*([MF])", text, re.I)
        if m_gen:
            out["gender"] = m_gen.group(1).upper()

        return out

    @staticmethod
    def _split_name(name_raw: str) -> tuple[str, str]:
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
