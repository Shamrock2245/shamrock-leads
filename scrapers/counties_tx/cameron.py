"""
Cameron County (TX) Arrest Scraper — Brownsville CCSO inmate list.

Portal: https://cameroncountytx.gov/os/inmates/
Roster table: Booking date | Name + BN#/SON# | Charges + Bond Amount
Updated ~every 2 hours (current + last 7 days).
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import List

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

PORTAL_URL = "https://cameroncountytx.gov/os/inmates/"
FACILITY = "Cameron County Jail"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class CameronScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Cameron"

    @property
    def state(self) -> str:
        return "TX"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        seen: set = set()
        try:
            resp = requests.get(PORTAL_URL, headers=HEADERS, timeout=45)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table")
            if not table:
                logger.error("Cameron: no inmate table")
                return []

            for tr in table.find_all("tr"):
                cells = tr.find_all("td")
                if len(cells) < 2:
                    continue
                book_date = cells[0].get_text(" ", strip=True)
                name_cell = cells[1]
                charge_cell = cells[2] if len(cells) > 2 else None

                name_raw = name_cell.get_text("\n", strip=True)
                lines = [ln.strip() for ln in name_raw.splitlines() if ln.strip()]
                name = lines[0] if lines else ""
                bn = ""
                for ln in lines[1:]:
                    m = re.search(r"BN#\s*:?\s*(\d+)", ln, re.I)
                    if m:
                        bn = m.group(1)
                        break
                if not name:
                    continue

                charge_text = charge_cell.get_text(" ", strip=True) if charge_cell else ""
                # strip leading dash on charge
                charge_text = re.sub(r"^\-\s*", "", charge_text).strip()
                bond = "0"
                bm = re.search(r"Bond Amount\s*\[\s*\$\s*([\d,]*)\s*\]", charge_text, re.I)
                if not bm:
                    bm = re.search(r"\$\s*([\d,]+)", charge_text)
                if bm and bm.group(1):
                    bond = bm.group(1).replace(",", "")

                # primary charge: first token-ish segment before case numbers
                charges = charge_text
                charges = re.sub(r"Bond Amount\[.*?\]", "", charges, flags=re.I).strip()

                booking = bn or hashlib.sha1(
                    f"cameron|{name}|{book_date}".encode()
                ).hexdigest()[:12]
                if booking in seen:
                    continue
                seen.add(booking)

                first, middle, last = self._pn(name)
                records.append(
                    ArrestRecord(
                        County=self.county,
                        State="TX",
                        Full_Name=name.title() if name.isupper() else name,
                        First_Name=first,
                        Middle_Name=middle,
                        Last_Name=last,
                        Booking_Number=str(booking),
                        Booking_Date=book_date,
                        Charges=charges or "Unknown",
                        Bond_Amount=bond,
                        Status="In Custody",
                        Facility=FACILITY,
                        Detail_URL=PORTAL_URL,
                        LastCheckedMode="INITIAL",
                    )
                )
        except Exception as e:
            logger.error(f"Cameron scrape failed: {e}")

        logger.info(f"✅ Cameron (TX): {len(records)} records in {time.time() - start:.1f}s")
        return records

    @staticmethod
    def _pn(n: str):
        n = " ".join((n or "").strip().split())
        # often "LAST FIRST MIDDLE" without comma
        if "," in n:
            last, rest = n.split(",", 1)
            p = rest.strip().split()
            return (p[0] if p else ""), (" ".join(p[1:]) if len(p) > 1 else ""), last.strip().title()
        p = n.split()
        if len(p) >= 2:
            # Cameron format: LAST FIRST MIDDLE
            return p[1].title(), (" ".join(p[2:]).title() if len(p) > 2 else ""), p[0].title()
        return "", "", n.title()
