"""
Aiken County (SC) Arrest Scraper.

Public page embeds iframe:
  https://lookups.aikencountysc.gov/DTNSearch/dtnSchInmSchPublicFlex.php

Direct TLS from some environments fails (SSLEOF). Uses curl_cffi when available;
falls back to empty with loud log if unreachable.
"""
from __future__ import annotations

import logging
import re
import time
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord
import hashlib

logger = logging.getLogger(__name__)
PORTAL_URL = "https://www.aikencountysheriff.net/185/Inmate-Search"
IFRAME_URL = "https://lookups.aikencountysc.gov/DTNSearch/dtnSchInmSchPublicFlex.php"


class AikenScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Aiken"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        html = self._fetch(IFRAME_URL)
        if not html:
            logger.warning("Aiken: iframe portal unreachable (TLS/network).")
            return []

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                if len(rows) < 2:
                    continue
                for row in rows[1:]:
                    cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                    if len(cells) < 2:
                        continue
                    name = cells[0]
                    if not name or len(name) < 2:
                        continue
                    booking = cells[1] if len(cells) > 1 else f"AIK_{hashlib.md5(f'{name}|AIKEN'.encode()).hexdigest()[:10]}"
                    charges = cells[2] if len(cells) > 2 else "Unknown"
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
            logger.error(f"Aiken parse failed: {e}")

        logger.info(f"Aiken: {len(records)} records in {time.time() - start:.1f}s")
        return records

    def _fetch(self, url: str) -> str:
        try:
            from curl_cffi import requests as cr
            resp = cr.get(url, timeout=25, impersonate="chrome131", verify=False)
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            logger.debug(f"Aiken curl_cffi failed: {e}")
        try:
            import requests
            resp = requests.get(
                url,
                timeout=20,
                verify=False,
                headers={"User-Agent": "Mozilla/5.0 Chrome/131.0.0.0"},
            )
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            logger.debug(f"Aiken requests failed: {e}")
        return ""
