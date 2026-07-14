"""
Durham County (NC) Arrest Scraper — Inmate Population Search (ASP.NET).
URL: https://www2.dconc.gov/sheriff/ips/default.aspx
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
PORTAL_URL = "https://www2.dconc.gov/sheriff/ips/default.aspx"


class DurhamScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Durham"

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
            for letter in string.ascii_uppercase:
                try:
                    page = self._search(session, letter)
                except Exception as e:
                    logger.debug(f"Durham {letter}: {e}")
                    continue
                for rec in page:
                    key = rec.Booking_Number or rec.Full_Name
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(rec)
                time.sleep(0.25)
        except Exception as e:
            logger.error(f"Durham scrape failed: {e}")
        logger.info(f"Durham: {len(records)} records in {time.time()-start:.1f}s")
        return records

    def _search(self, session: requests.Session, last_name: str) -> List[ArrestRecord]:
        resp = session.get(PORTAL_URL, timeout=25, verify=False)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        data: Dict[str, str] = {}
        for inp in soup.find_all("input"):
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
        # common ASP.NET search buttons
        for inp in soup.find_all("input", {"type": re.compile(r"submit|button", re.I)}):
            if inp.get("name") and "search" in (inp.get("name")+str(inp.get("value",""))).lower():
                data[inp["name"]] = inp.get("value") or "Search"
                break
        else:
            data["__EVENTTARGET"] = data.get("__EVENTTARGET", "")
        resp = session.post(PORTAL_URL, data=data, timeout=35, verify=False)
        soup = BeautifulSoup(resp.text, "html.parser")
        out: List[ArrestRecord] = []
        for table in soup.find_all("table"):
            for tr in table.find_all("tr")[1:]:
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) < 2:
                    continue
                name = cells[0]
                if not name or len(name) < 2:
                    continue
                out.append(ArrestRecord(
                    County=self.county, State="NC", Full_Name=name,
                    Booking_Number=str(cells[1] if len(cells) > 1 else f"DUR_{hashlib.md5(f"{name}|DURHAM".encode()).hexdigest()[:10]}"),
                    Charges=cells[2] if len(cells) > 2 else "Unknown",
                    Bond_Amount=re.sub(r"[^\d.]", "", cells[3] if len(cells) > 3 else "0") or "0",
                    Status="In Custody", Detail_URL=PORTAL_URL,
                    Facility="Durham County Detention",
                ))
            if out:
                break
        return out
