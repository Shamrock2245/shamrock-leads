"""
Pitt County (NC) Arrest Scraper — Detainee Search ASP.NET GridView.

URL: https://apps.pittcountync.gov/apps/detention/detainee/

Strategy: blank-field search returns all active detainees, but ASP.NET page
postbacks error out. Letter-prefix last-name searches work and cover the
roster (partial names accepted). Dense letters that fill a page (10 rows)
get digraph expansion (Aa–Az) for better coverage.
"""
from __future__ import annotations

import logging
import string
import time
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

PORTAL_URL = "https://apps.pittcountync.gov/apps/detention/detainee/"
PAGE_SIZE_HINT = 10  # default GridView page size when page-size postback fails


class PittScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Pitt"

    @property
    def state(self) -> str:
        return "NC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        })

        all_rows: Dict[str, dict] = {}
        try:
            for letter in string.ascii_uppercase:
                rows = self._search(session, last_name=letter)
                for row in rows:
                    all_rows[row["booking"]] = row

                # Digraph expand when a letter saturates the page
                if len(rows) >= PAGE_SIZE_HINT:
                    for second in string.ascii_lowercase:
                        digraph = f"{letter}{second}"
                        sub = self._search(session, last_name=digraph)
                        for row in sub:
                            all_rows[row["booking"]] = row
                        if not sub:
                            # remaining digraphs for this letter unlikely
                            if second > "c":
                                break
                        time.sleep(0.15)

                time.sleep(0.2)
        except Exception as e:
            logger.error("Pitt scrape failed: %s", e)

        records = [self._to_record(r) for r in all_rows.values()]
        logger.info(
            "Pitt: %d records in %.1fs",
            len(records),
            time.time() - start,
        )
        return records

    def _search(self, session: requests.Session, last_name: str) -> List[dict]:
        try:
            resp = session.get(PORTAL_URL, timeout=40)
            resp.raise_for_status()
        except Exception as e:
            logger.debug("Pitt GET failed: %s", e)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        data = self._form_data(soup)
        data["ctl00$mainContent$bookNumTextBox"] = ""
        data["ctl00$mainContent$lastNameTextBox"] = last_name
        data["ctl00$mainContent$firstNameTextBox"] = ""
        data["ctl00$mainContent$Button3"] = "Get  Detainee"

        try:
            resp = session.post(PORTAL_URL, data=data, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            logger.debug("Pitt POST %r failed: %s", last_name, e)
            return []

        return self._parse_grid(BeautifulSoup(resp.text, "html.parser"))

    @staticmethod
    def _form_data(soup: BeautifulSoup) -> Dict[str, str]:
        data: Dict[str, str] = {}
        for inp in soup.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            typ = (inp.get("type") or "text").lower()
            if typ in ("submit", "button", "image"):
                continue
            data[name] = inp.get("value") or ""
        for sel in soup.find_all("select"):
            name = sel.get("name")
            if not name:
                continue
            opt = sel.find("option", selected=True) or sel.find("option")
            data[name] = opt.get("value") if opt else ""
        return data

    @staticmethod
    def _parse_grid(soup: BeautifulSoup) -> List[dict]:
        table = soup.find("table", id="ctl00_mainContent_GridView1")
        if not table:
            return []
        out: List[dict] = []
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 7:
                continue
            # Columns: Select | Last | Suffix | First | Middle | DOB | Booking | Gender | Race
            if "Select" not in cells[0] and cells[0] != "":
                continue
            last = cells[1]
            suffix = cells[2]
            first = cells[3]
            middle = cells[4]
            dob = cells[5]
            booking = cells[6]
            gender = cells[7] if len(cells) > 7 else ""
            race = cells[8] if len(cells) > 8 else ""
            if not last or not booking or not booking.isdigit():
                continue
            out.append({
                "last": last,
                "suffix": suffix,
                "first": first,
                "middle": middle,
                "dob": dob,
                "booking": booking,
                "gender": gender,
                "race": race,
            })
        return out

    def _to_record(self, row: dict) -> ArrestRecord:
        last = row["last"]
        first = row["first"]
        middle = row["middle"]
        suffix = row.get("suffix") or ""
        # Display name
        given = " ".join(p for p in (first, middle, suffix) if p).strip()
        full = f"{last}, {given}".strip(", ")

        return ArrestRecord(
            County=self.county,
            State="NC",
            Full_Name=full,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=str(row["booking"]),
            DOB=row.get("dob") or "",
            Sex=(row.get("gender") or "")[:1].upper(),
            Race=row.get("race") or "",
            Charges="Unknown",
            Bond_Amount="0",
            Status="In Custody",
            Detail_URL=PORTAL_URL,
            Facility="Pitt County Detention Center",
        )
