"""
Bexar County (TX) Arrest Scraper — Central Magistrate 24h Search.

Portal: https://centralmagistrate.bexar.org/
Shows Class B+ arrests processed by the Central Magistrate in the last 24 hours.
San Antonio / Bexar is a top-5 TX population county — high bail volume.

Columns: Name, Race, Age(DOB MMDDYYYY), SID, Booking Number
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import List, Tuple

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PORTAL_URL = "https://centralmagistrate.bexar.org/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


from scrapers.stealth_utils import make_stealth_request, BehaviorSimulator

class BexarScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Bexar"

    @property
    def state(self) -> str:
        return "TX"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []

        try:
            resp = make_stealth_request(PORTAL_URL, method="GET", timeout=30)
            if resp and resp.text:
                records = self._parse_table(resp.text)
        except Exception as e:
            logger.error(f"Bexar scrape failed: {e}")

        logger.info(
            f"✅ Bexar (TX): {len(records)} records in {time.time() - start:.1f}s"
        )
        return records

    def _parse_table(self, html: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        table = None
        for t in soup.find_all("table"):
            rows = t.find_all("tr")
            if not rows:
                continue
            headers = [
                th.get_text(" ", strip=True).lower()
                for th in rows[0].find_all(["th", "td"])
            ]
            joined = " ".join(headers)
            if "booking" in joined and "name" in joined:
                table = t
                break
            if "sid" in joined and "name" in joined:
                table = t
                break

        if table is None:
            # Fallback: largest table
            tables = soup.find_all("table")
            if not tables:
                return []
            table = max(tables, key=lambda t: len(t.find_all("tr")))

        rows = table.find_all("tr")
        if len(rows) < 2:
            return []

        headers = [
            th.get_text(" ", strip=True).lower()
            for th in rows[0].find_all(["th", "td"])
        ]
        out: List[ArrestRecord] = []
        seen = set()

        for row in rows[1:]:
            cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
            if len(cells) < 3:
                continue

            name = ""
            race = ""
            dob = ""
            sid = ""
            booking = ""
            for i, h in enumerate(headers):
                if i >= len(cells):
                    break
                val = cells[i]
                if h == "name":
                    name = val
                elif h == "race":
                    race = val
                elif h in ("age", "dob"):
                    dob = self._normalize_dob(val)
                elif h == "sid":
                    sid = val
                elif "booking" in h:
                    booking = val

            # Positional fallback: Name, Race, Age, SID, Booking Number
            if not name and cells:
                name = cells[0]
            if not race and len(cells) > 1:
                race = cells[1]
            if not dob and len(cells) > 2:
                dob = self._normalize_dob(cells[2])
            if not sid and len(cells) > 3:
                sid = cells[3]
            if not booking and len(cells) > 4:
                booking = cells[4]

            name = (name or "").strip()
            if not name or len(name) < 2:
                continue

            if not booking:
                booking = (
                    f"BEX_{hashlib.md5(f'{name}|{sid}|BEXAR'.encode()).hexdigest()[:10]}"
                )
            if booking in seen:
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
                    Booking_Number=str(booking).strip(),
                    Person_ID=str(sid or ""),
                    DOB=dob,
                    Race=race,
                    Charges="Unknown",  # magistrate list has no charge column
                    Bond_Amount="0",
                    Status="In Custody",
                    Facility="Bexar County Jail",
                    Agency="Bexar County Central Magistrate",
                    Detail_URL=PORTAL_URL,
                )
            )
        return out

    @staticmethod
    def _normalize_dob(raw: str) -> str:
        """Magistrate 'Age' column is often MMDDYYYY (e.g. 03151980)."""
        raw = (raw or "").strip()
        if re.fullmatch(r"\d{8}", raw):
            return f"{raw[0:2]}/{raw[2:4]}/{raw[4:8]}"
        return raw

    @staticmethod
    def _split_name(name: str) -> Tuple[str, str]:
        name = name.replace("\xa0", " ").strip()
        # "CABRAL, RONDA JO" or "CAMPOS, ALBERTO, IV"
        if "," in name:
            parts = [p.strip() for p in name.split(",")]
            last = parts[0].title()
            first = " ".join(parts[1:]).title()
            return first, last
        bits = name.split()
        if len(bits) >= 2:
            return bits[0].title(), bits[-1].title()
        return name.title(), ""
