"""
Darlington County (SC) Arrest Scraper.
Portal: https://bookings.darlingtonsheriff.org/dcn/ (DCN family; often slow).
"""
from __future__ import annotations

import logging
import re
import time
from typing import List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord
import hashlib

logger = logging.getLogger(__name__)
PORTAL_URL = "https://bookings.darlingtonsheriff.org/dcn/"


class DarlingtonScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Darlington"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        records: List[ArrestRecord] = []
        try:
            for path in ("", "inmates", "Inmates.aspx", "default.aspx"):
                url = urljoin(PORTAL_URL, path)
                try:
                    resp = session.get(url, timeout=40, verify=False)
                except Exception:
                    continue
                if resp.status_code != 200 or len(resp.text) < 500:
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")
                for table in soup.find_all("table"):
                    for tr in table.find_all("tr")[1:]:
                        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                        if len(cells) < 2:
                            continue
                        name = cells[0]
                        if not name or len(name) < 2:
                            continue
                        booking = cells[1] if len(cells) > 1 else f"DAR_{hashlib.md5(f'{name}|DARLIN'.encode()).hexdigest()[:10]}"
                        charges = cells[2] if len(cells) > 2 else "Unknown"
                        bond = re.sub(r"[^\d.]", "", cells[3] if len(cells) > 3 else "0") or "0"
                        records.append(ArrestRecord(
                            County=self.county, State="SC", Full_Name=name,
                            Booking_Number=str(booking), Charges=charges,
                            Bond_Amount=bond, Status="In Custody", Detail_URL=url,
                        ))
                    if records:
                        break
                if records:
                    break
            if not records:
                logger.warning("Darlington: no roster rows (timeout/empty portal)")
        except Exception as e:
            logger.error(f"Darlington scrape failed: {e}")
        logger.info(f"Darlington: {len(records)} records in {time.time()-start:.1f}s")
        return records
