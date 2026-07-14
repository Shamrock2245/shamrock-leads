"""
Jasper County (SC) Arrest Scraper.
Platform: Custom WordPress roster cards — jasperso.com/inmate-roster/
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

logger = logging.getLogger(__name__)
PORTAL_URL = "https://jasperso.com/inmate-roster/"


class JasperScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Jasper"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        })
        try:
            resp = session.get(PORTAL_URL, timeout=25)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select("div.inmate, div.col-sm-4.inmate")
            if not cards:
                # broader fallback
                cards = [
                    d for d in soup.find_all("div")
                    if "Arrest #" in d.get_text() and len(d.get_text()) < 2000
                ]

            for card in cards:
                text = card.get_text("\n", strip=True)
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                if not lines:
                    continue
                name = lines[0]
                if name.upper() in ("INMATE ROSTER", "ALL INMATES", "48 HOUR RELEASE"):
                    continue

                arrest_num = self._field(text, r"Arrest\s*#\s*:?\s*(\S+)")
                arrest_date = self._field(text, r"Arrest\s*Date\s*:?\s*([0-9/.\-]+)")
                gender = self._field(text, r"Gender\s*:?\s*(\w+)")
                race = self._field(text, r"Race\s*:?\s*([^\n]+)")
                age = self._field(text, r"Age\s*:?\s*(\d+)")
                agency = self._field(text, r"Arrest\s*Agency\s*:?\s*([^\n]+)")
                charges = self._field(text, r"Charges?\s*:?\s*([^\n]+)")
                bond = self._field(text, r"Bond\s*:?\s*\$?([0-9,.\s]+)")

                if not arrest_num:
                    arrest_num = f"JAS_{re.sub(r'[^A-Za-z0-9]', '', name)[:16]}_{abs(hash(text)) % 100000}"

                first = last = middle = ""
                if "," in name:
                    last, rest = [p.strip() for p in name.split(",", 1)]
                    parts = rest.split()
                    first = parts[0] if parts else ""
                    middle = " ".join(parts[1:]) if len(parts) > 1 else ""
                else:
                    parts = name.split()
                    first = parts[0] if parts else ""
                    last = parts[-1] if len(parts) > 1 else ""

                records.append(
                    ArrestRecord(
                        County=self.county,
                        State="SC",
                        Full_Name=name,
                        First_Name=first,
                        Middle_Name=middle,
                        Last_Name=last,
                        Booking_Number=str(arrest_num),
                        Arrest_Date=arrest_date or "",
                        Booking_Date=arrest_date or "",
                        Sex=(gender or "")[:1].upper(),
                        Race=(race or "").strip(),
                        Age_At_Arrest=age or "",
                        Agency=(agency or "").strip(),
                        Charges=charges or "Unknown",
                        Bond_Amount=re.sub(r"[^\d.]", "", bond or "") or "0",
                        Status="In Custody",
                        Detail_URL=PORTAL_URL,
                    )
                )
        except Exception as e:
            logger.error(f"Jasper scrape failed: {e}")

        logger.info(f"Jasper: {len(records)} records in {time.time() - start:.1f}s")
        return records

    @staticmethod
    def _field(text: str, pattern: str) -> str:
        m = re.search(pattern, text, re.I)
        return m.group(1).strip() if m else ""
