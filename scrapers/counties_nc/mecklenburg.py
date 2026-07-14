"""
Mecklenburg County (NC) Arrest Scraper — MCSO custom portal.
URL: https://mecksheriffweb.mecklenburgcountync.gov/Inmate
"""
from __future__ import annotations

import logging
import re
import string
import time
from typing import Dict, List

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord
import hashlib

logger = logging.getLogger(__name__)
PORTAL_URL = "https://mecksheriffweb.mecklenburgcountync.gov/Inmate"


class MecklenburgScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Mecklenburg"

    @property
    def state(self) -> str:
        return "NC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": PORTAL_URL,
        })
        records: List[ArrestRecord] = []
        seen = set()
        try:
            # Landing + letter walk if form present
            for letter in list(string.ascii_uppercase) + [""]:
                try:
                    page_records = self._search(session, letter)
                except Exception as e:
                    logger.debug(f"Mecklenburg {letter!r}: {e}")
                    continue
                for rec in page_records:
                    key = rec.Booking_Number or rec.Full_Name
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(rec)
                time.sleep(0.25)
        except Exception as e:
            logger.error(f"Mecklenburg scrape failed: {e}")
        logger.info(f"Mecklenburg: {len(records)} records in {time.time()-start:.1f}s")
        return records

    def _search(self, session: requests.Session, last_name: str) -> List[ArrestRecord]:
        resp = session.get(PORTAL_URL, timeout=25)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        form = soup.find("form")
        if not form:
            return self._parse_tables(soup, PORTAL_URL)
        action = form.get("action") or PORTAL_URL
        if not action.startswith("http"):
            action = requests.compat.urljoin(PORTAL_URL, action)
        data: Dict[str, str] = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            typ = (inp.get("type") or "text").lower()
            if typ in ("submit", "button", "image"):
                continue
            data[name] = inp.get("value") or ""
        for name in list(data.keys()):
            ln = name.lower()
            if "last" in ln:
                data[name] = last_name
            elif "first" in ln:
                data[name] = ""
        for inp in form.find_all("input", {"type": re.compile(r"submit|button", re.I)}):
            if inp.get("name"):
                data[inp["name"]] = inp.get("value") or "Search"
                break
        resp = session.post(action, data=data, timeout=35)
        return self._parse_tables(BeautifulSoup(resp.text, "html.parser"), action)

    def _parse_tables(self, soup: BeautifulSoup, source: str) -> List[ArrestRecord]:
        out: List[ArrestRecord] = []
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
                booking = cells[1] if len(cells) > 1 else f"MECK_{hashlib.md5(f"{name}|MECKLE".encode()).hexdigest()[:10]}"
                charges = "Unknown"
                bond = "0"
                for c in cells[2:]:
                    if re.search(r"\d", c) and "$" in c:
                        bond = re.sub(r"[^\d.]", "", c) or "0"
                    elif len(c) > 5 and re.search(r"[A-Za-z]{3,}", c):
                        charges = c
                out.append(ArrestRecord(
                    County=self.county, State="NC", Full_Name=name,
                    Booking_Number=str(booking), Charges=charges, Bond_Amount=bond,
                    Status="In Custody", Detail_URL=source,
                    Facility="Mecklenburg County Detention",
                ))
            if out:
                break
        return out
