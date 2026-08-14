"""
Johnston County (NC) Arrest Scraper — JCSO ColdFusion inmate roster.

Portal: https://www.johnstonnc.com/sheriffs_office/b_jailsearch2s.cfm?sb=fn
Detail:  https://www.johnstonnc.com/sheriffs_office/b_jailsearch3.cfm?nameid=…

Full active roster HTML table (Name, Primary Charge, Arrest Date).
"""
from __future__ import annotations

import logging
import re
import time
from typing import List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.johnstonnc.com/sheriffs_office/b_jailsearch2s.cfm?sb=fn"
BASE = "https://www.johnstonnc.com/sheriffs_office/"
FACILITY = "Johnston County Jail"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class JohnstonScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Johnston"

    @property
    def state(self) -> str:
        return "NC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(HEADERS)
        records: List[ArrestRecord] = []
        seen: set = set()

        try:
            resp = session.get(ROSTER_URL, timeout=40)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table")
            if not table:
                logger.error("Johnston: no roster table")
                return []

            for tr in table.find_all("tr"):
                cells = tr.find_all("td")
                if len(cells) < 3:
                    continue
                name = cells[0].get_text(" ", strip=True)
                charge = cells[1].get_text(" ", strip=True) if len(cells) > 1 else ""
                arrest_date = cells[2].get_text(" ", strip=True) if len(cells) > 2 else ""
                if not name or name.lower().startswith("sort") or "individuals listed" in name.lower():
                    continue
                if name.lower() in ("name", "primary charge", "arrest date"):
                    continue

                link = tr.find("a", href=True)
                detail = urljoin(BASE, link["href"]) if link else ROSTER_URL
                nameid = ""
                if link:
                    m = re.search(r"nameid=(\d+)", link["href"])
                    if m:
                        nameid = m.group(1)

                booking = nameid
                if not booking:
                    continue
                if booking in seen:
                    continue
                seen.add(booking)

                first, middle, last = self._parse_name(name)
                records.append(
                    ArrestRecord(
                        County=self.county,
                        State="NC",
                        Full_Name=name,
                        First_Name=first,
                        Middle_Name=middle,
                        Last_Name=last,
                        Booking_Number=str(booking),
                        Booking_Date=arrest_date,
                        Charges=charge or "Unknown",
                        Bond_Amount="0",
                        Status="In Custody",
                        Facility=FACILITY,
                        Detail_URL=detail,
                        LastCheckedMode="INITIAL",
                    )
                )
        except Exception as e:
            logger.error(f"Johnston scrape failed: {e}")

        logger.info(f"✅ Johnston (NC): {len(records)} records in {time.time() - start:.1f}s")
        return records

    @staticmethod
    def _parse_name(n: str):
        n = " ".join((n or "").strip().split())
        if not n:
            return "", "", ""
        if "," in n:
            last, rest = n.split(",", 1)
            parts = rest.strip().split()
            return (parts[0] if parts else ""), (" ".join(parts[1:]) if len(parts) > 1 else ""), last.strip()
        parts = n.split()
        if len(parts) == 1:
            return "", "", parts[0]
        return parts[0], (" ".join(parts[1:-1]) if len(parts) > 2 else ""), parts[-1]
