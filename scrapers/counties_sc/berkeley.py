"""
Berkeley County (SC) Arrest Scraper.
Portal: https://sheriff.berkeleycountysc.gov/report/inmate-lookup/
Often blocked (403/Cloudflare) from datacenter IPs. Attempts HTTP fetch;
set BERKELEY_SOCKS_PROXY or SOCKS_PROXY for residential egress.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import List

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord
import hashlib

logger = logging.getLogger(__name__)
PORTAL_URL = "https://sheriff.berkeleycountysc.gov/report/inmate-lookup/"


class BerkeleyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Berkeley"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        socks = (
            os.getenv("BERKELEY_SOCKS_PROXY")
            or os.getenv("SOCKS_PROXY")
            or os.getenv("RESIDENTIAL_SOCKS")
            or None
        )
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        })
        proxies = {"http": socks, "https": socks} if socks else None
        try:
            resp = session.get(PORTAL_URL, timeout=25, proxies=proxies, verify=False)
            if resp.status_code in (403, 503):
                logger.warning(
                    f"Berkeley: HTTP {resp.status_code} — bot protection. "
                    f"Set SOCKS_PROXY for residential IP."
                )
                return []
            resp.raise_for_status()
            records = self._parse(resp.text)
            logger.info(f"Berkeley: {len(records)} records in {time.time()-start:.1f}s")
            return records
        except Exception as e:
            logger.error(f"Berkeley scrape failed: {e}")
            return []

    def _parse(self, html: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        records: List[ArrestRecord] = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            for tr in rows[1:]:
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) < 2:
                    continue
                name = cells[0]
                if not name or len(name) < 2:
                    continue
                charges = cells[1] if len(cells) > 1 else "Unknown"
                booking = cells[2] if len(cells) > 2 else f"SC_{hashlib.md5(f"{name}|BERKEL".encode()).hexdigest()[:10]}"
                bond = re.sub(r"[^\d.]", "", cells[3] if len(cells) > 3 else "0") or "0"
                records.append(ArrestRecord(
                    County=self.county,
                    State="SC",
                    Full_Name=name,
                    Booking_Number=str(booking),
                    Charges=charges,
                    Bond_Amount=bond,
                    Status="In Custody",
                    Detail_URL=PORTAL_URL,
                ))
            if records:
                break
        return records
