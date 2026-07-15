"""
Knox County (TN) Arrest Scraper — Sheriff Inmate Population / 24hr Arrests.

Portal: https://sheriff.knoxcountytn.gov/inmate.php
Alt:    https://sheriff.knoxcountytn.gov/index.php  (24hr arrests)

Letter index: inmate.php?letter=A … Z
Site occasionally serves a maintenance placeholder ("Page refreshing").
Scraper fails closed with empty list when roster HTML is unavailable.
"""
from __future__ import annotations

import hashlib
import logging
import re
import string
import time
from typing import List, Tuple

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

INMATE_URL = "https://sheriff.knoxcountytn.gov/inmate.php"
ARREST_URL = "https://sheriff.knoxcountytn.gov/index.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class KnoxScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Knox"

    @property
    def state(self) -> str:
        return "TN"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(HEADERS)
        session.verify = False
        records: List[ArrestRecord] = []
        seen = set()

        for letter in string.ascii_uppercase:
            try:
                url = f"{INMATE_URL}?letter={letter}"
                resp = session.get(url, timeout=25)
                if resp.status_code != 200:
                    continue
                if self._is_maintenance(resp.text):
                    logger.warning("Knox: roster in maintenance mode — aborting letter walk")
                    break
                batch = self._parse_inmate_html(resp.text, source_url=url)
                for rec in batch:
                    if rec.Booking_Number in seen:
                        continue
                    seen.add(rec.Booking_Number)
                    records.append(rec)
                time.sleep(0.2)
            except Exception as e:
                logger.debug(f"Knox letter {letter}: {e}")

        if not records:
            # Fallback: 24-hour arrest page
            try:
                resp = session.get(ARREST_URL, timeout=25)
                if resp.status_code == 200 and not self._is_maintenance(resp.text):
                    batch = self._parse_inmate_html(resp.text, source_url=ARREST_URL)
                    for rec in batch:
                        if rec.Booking_Number in seen:
                            continue
                        seen.add(rec.Booking_Number)
                        records.append(rec)
            except Exception as e:
                logger.debug(f"Knox 24hr fallback: {e}")

        logger.info(f"✅ Knox (TN): {len(records)} records in {time.time() - start:.1f}s")
        return records

    @staticmethod
    def _is_maintenance(html: str) -> bool:
        low = html.lower()
        return "page refreshing" in low or "check back momentarily" in low

    def _parse_inmate_html(self, html: str, source_url: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        records: List[ArrestRecord] = []

        # Pattern from live roster (when available): name headers + IDN# + charges/bond
        # Try structured tables first
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [
                th.get_text(" ", strip=True).lower()
                for th in rows[0].find_all(["th", "td"])
            ]
            if not any(k in " ".join(headers) for k in ("name", "inmate", "idn", "charge")):
                continue
            for row in rows[1:]:
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue
                rec = self._row_to_record(cells, headers, source_url)
                if rec:
                    records.append(rec)
            if records:
                return records

        # Free-text / card pattern: NAME + IDN# + Bond Amount
        text = soup.get_text("\n", strip=True)
        # NAME line often ALL CAPS LAST, FIRST
        blocks = re.split(r"\n(?=[A-Z][A-Z' \-]+,\s+[A-Z])", text)
        for block in blocks:
            name_m = re.match(
                r"([A-Z][A-Z' \-]+,\s+[A-Z][A-Za-z' \-\.]+)",
                block,
            )
            if not name_m:
                continue
            name = name_m.group(1).strip()
            idn_m = re.search(r"IDN#\s*:?\s*(\d+)", block, re.I)
            bond_m = re.search(
                r"Bond Amount\s*:?\s*\$?\s*([\d,]+\.?\d*|None)",
                block,
                re.I,
            )
            charge_m = re.search(
                r"Charge\s+([A-Z][^\n]{3,120})",
                block,
            )
            booking = idn_m.group(1) if idn_m else (
                f"KNX_{hashlib.md5(f'{name}|KNOX_TN'.encode()).hexdigest()[:10]}"
            )
            bond_raw = bond_m.group(1) if bond_m else "0"
            if bond_raw.lower() == "none":
                bond = "0"
            else:
                bond = re.sub(r"[^\d.]", "", bond_raw) or "0"
            charges = charge_m.group(1).strip() if charge_m else "Unknown"
            # Strip bond noise from charge line
            charges = re.split(r"\s+Bond\s+", charges, maxsplit=1)[0].strip()

            first, last = self._split_name(name)
            records.append(
                ArrestRecord(
                    County=self.county,
                    State="TN",
                    Full_Name=name.title(),
                    First_Name=first,
                    Last_Name=last,
                    Booking_Number=str(booking),
                    Charges=charges or "Unknown",
                    Bond_Amount=bond,
                    Status="In Custody",
                    Facility="Knox County Jail",
                    Agency="Knox County Sheriff's Office",
                    Detail_URL=source_url,
                )
            )
        return records

    def _row_to_record(self, cells, headers, source_url: str):
        name = cells[0]
        if not name or len(name) < 2:
            return None
        booking = ""
        charges = "Unknown"
        bond = "0"
        for i, h in enumerate(headers):
            if i >= len(cells):
                break
            val = cells[i]
            if "idn" in h or ("book" in h and "date" not in h):
                booking = val
            elif "charge" in h or "offense" in h:
                charges = val
            elif "bond" in h:
                bond = re.sub(r"[^\d.]", "", val) or "0"
        if not booking:
            booking = f"KNX_{hashlib.md5(f'{name}|KNOX_TN'.encode()).hexdigest()[:10]}"
        first, last = self._split_name(name)
        return ArrestRecord(
            County=self.county,
            State="TN",
            Full_Name=name.title() if name.isupper() else name,
            First_Name=first,
            Last_Name=last,
            Booking_Number=str(booking),
            Charges=charges or "Unknown",
            Bond_Amount=bond,
            Status="In Custody",
            Facility="Knox County Jail",
            Agency="Knox County Sheriff's Office",
            Detail_URL=source_url,
        )

    @staticmethod
    def _split_name(name: str) -> Tuple[str, str]:
        if "," in name:
            parts = name.split(",", 1)
            return parts[1].strip().title(), parts[0].strip().title()
        bits = name.split()
        if len(bits) >= 2:
            return bits[0].title(), bits[-1].title()
        return name.title(), ""
