"""
Marlboro County (SC) Arrest Scraper.
Portal: https://marlborocountyjailsc.org/ (Cloudflare/403 from datacenter IPs).
"""
from __future__ import annotations

import logging
import re
import time
from typing import List

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord
import hashlib

logger = logging.getLogger(__name__)
PORTAL_URL = "https://marlborocountyjailsc.org/"


class MarlboroScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Marlboro"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            })
            resp = session.get(PORTAL_URL, timeout=20)
            if resp.status_code in (403, 503):
                logger.warning(
                    f"Marlboro: HTTP {resp.status_code} — likely bot protection. "
                    "Needs residential proxy / browser path."
                )
                return []
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                if len(rows) < 2:
                    continue
                for row in rows[1:]:
                    cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                    if len(cells) < 2:
                        continue
                    name = cells[0]
                    charges = cells[1] if len(cells) > 1 else "Unknown"
                    booking = cells[2] if len(cells) > 2 else f"MAR_{hashlib.md5(f'{name}|MARLBO'.encode()).hexdigest()[:10]}"
                    bond = re.sub(r"[^\d.]", "", cells[3] if len(cells) > 3 else "0") or "0"
                    records.append(
                        ArrestRecord(
                            County=self.county,
                            State="SC",
                            Full_Name=name,
                            Booking_Number=str(booking),
                            Charges=charges,
                            Bond_Amount=bond,
                            Status="In Custody",
                            Detail_URL=PORTAL_URL,
                        )
                    )
                if records:
                    break
        except Exception as e:
            logger.error(f"Marlboro scrape failed: {e}")
        logger.info(f"Marlboro: {len(records)} records in {time.time() - start:.1f}s")
        return records
