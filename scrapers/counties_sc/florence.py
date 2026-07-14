"""
Florence County (SC) Arrest Scraper.

Platform: ASP.NET / DevExpress grid at booking.fcso.org
Search via InmatesSearchBox + InmatesSearchButton postback (letter walk).
"""
from __future__ import annotations

import logging
import re
import string
import time
from typing import Dict, List, Optional, Set

import requests
import urllib3
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)

PORTAL_URL = "https://booking.fcso.org/index"
REQUEST_PAUSE_S = 0.3


class FlorenceScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Florence"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": PORTAL_URL,
            "Origin": "https://booking.fcso.org",
        })

        seen: Set[str] = set()
        records: List[ArrestRecord] = []

        try:
            for letter in string.ascii_uppercase:
                try:
                    rows = self._search_letter(session, letter)
                except Exception as e:
                    logger.warning(f"Florence letter {letter}: {e}")
                    continue
                for row in rows:
                    key = f"{row['name']}|{row.get('booked','')}|{row.get('age','')}"
                    if key in seen:
                        continue
                    seen.add(key)
                    rec = self._to_record(row)
                    if rec:
                        records.append(rec)
                time.sleep(REQUEST_PAUSE_S)
        except Exception as e:
            logger.error(f"Florence scrape failed: {e}")

        logger.info(f"Florence: {len(records)} records in {time.time() - start:.1f}s")
        return records

    def _search_letter(self, session: requests.Session, letter: str) -> List[dict]:
        resp = session.get(PORTAL_URL, timeout=25, verify=False)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        data = self._all_fields(soup)
        if not data.get("__VIEWSTATE"):
            raise RuntimeError("missing ViewState")

        data["InmatesSearchBox"] = letter
        data["__EVENTTARGET"] = "InmatesSearchButton"
        data["__EVENTARGUMENT"] = ""
        # clear other search boxes
        if "ChargesSearchBox" in data:
            data["ChargesSearchBox"] = ""
        if "DaysSearchBox" in data:
            data["DaysSearchBox"] = ""

        resp = session.post(PORTAL_URL, data=data, timeout=40, verify=False)
        resp.raise_for_status()
        return self._parse_grid(BeautifulSoup(resp.text, "html.parser"))

    @staticmethod
    def _all_fields(soup: BeautifulSoup) -> Dict[str, str]:
        data: Dict[str, str] = {}
        for inp in soup.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            typ = (inp.get("type") or "text").lower()
            if typ in ("submit", "button", "image"):
                continue
            data[name] = inp.get("value") or ""
        return data

    def _parse_grid(self, soup: BeautifulSoup) -> List[dict]:
        rows_out: List[dict] = []
        # DevExpress data rows
        data_rows = soup.find_all("tr", class_=re.compile(r"dxgvDataRow|DataRow", re.I))
        if not data_rows:
            grid = soup.find("table", id=re.compile(r"gvInmates", re.I))
            if grid:
                data_rows = grid.find_all("tr")[1:]

        for tr in data_rows:
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 2:
                continue
            name = cells[0]
            if not name or not re.search(r"[A-Za-z]", name):
                continue
            if name.lower() in ("name", "inmate"):
                continue
            rows_out.append({
                "name": name,
                "age": cells[1] if len(cells) > 1 else "",
                "race": cells[2] if len(cells) > 2 else "",
                "sex": cells[3] if len(cells) > 3 else "",
                "booked": cells[4] if len(cells) > 4 else "",
            })
        return rows_out

    def _to_record(self, row: dict) -> Optional[ArrestRecord]:
        name = row["name"]
        first = last = middle = ""
        if "," in name:
            last, rest = [p.strip() for p in name.split(",", 1)]
            parts = rest.split()
            first = parts[0] if parts else ""
            middle = " ".join(parts[1:]) if len(parts) > 1 else ""
        else:
            parts = name.split()
            first = parts[0] if parts else ""
            last = parts[-1] if len(parts) > 1 else name

        booked = row.get("booked") or ""
        booking_num = (
            f"FLO_{re.sub(r'[^A-Za-z0-9]', '', last)[:12]}_"
            f"{re.sub(r'[^0-9]', '', booked)[:8]}_"
            f"{re.sub(r'[^0-9]', '', row.get('age') or '') or '0'}"
        )
        return ArrestRecord(
            County=self.county,
            State="SC",
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=booking_num,
            Booking_Date=booked,
            Arrest_Date=booked,
            Age_At_Arrest=str(row.get("age") or ""),
            Race=str(row.get("race") or ""),
            Sex=(row.get("sex") or "")[:1].upper(),
            Charges="Unknown",
            Bond_Amount="0",
            Status="In Custody",
            Detail_URL=PORTAL_URL,
            Facility="Florence County Detention Center",
        )
