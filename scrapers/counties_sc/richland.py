"""
Richland County (SC) Arrest Scraper.

Platform: ASP.NET JMSOnline public offender search
URL: https://www7.richlandcountysc.gov/JMSOnline/public/default.aspx

Captcha: the bitmap captcha renders the same token stored in
``hidStrRandom`` — submit that value as ``txtBitMapCaptcha``.

Coverage strategy: two-letter last-name prefixes (A–Z × a–z) instead of
GridView pagination (Page$N postbacks fail ASP.NET event validation).
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

PORTAL_URL = "https://www7.richlandcountysc.gov/JMSOnline/public/default.aspx"
REQUEST_PAUSE_S = 0.25
# Second character set for digraph search (keeps first-page results under 10 rows)
SECOND_CHARS = string.ascii_lowercase + string.digits


class RichlandSCScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Richland"

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
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Origin": "https://www7.richlandcountysc.gov",
            "Referer": PORTAL_URL,
        })

        seen: Set[str] = set()
        records: List[ArrestRecord] = []

        def ingest(rows: List[dict]) -> None:
            for row in rows:
                dedup = f"{row['name']}|{row['booked']}|{row['age']}"
                if dedup in seen:
                    continue
                seen.add(dedup)
                rec = self._to_record(row)
                if rec:
                    records.append(rec)

        try:
            # 1) Single-letter searches (first page each)
            # 2) If that letter has GridView pager links, digraph-expand to
            #    recover names beyond page 1 (Page$N postbacks fail event validation).
            for letter in string.ascii_uppercase:
                try:
                    rows, has_pager = self._search_query(session, letter)
                except Exception as e:
                    logger.warning(f"Richland letter {letter!r} failed: {e}")
                    continue
                ingest(rows)
                time.sleep(REQUEST_PAUSE_S)

                if has_pager:
                    for b in SECOND_CHARS:
                        q = f"{letter}{b}"
                        try:
                            drows, _ = self._search_query(session, q)
                        except Exception as e:
                            logger.debug(f"Richland digraph {q!r} failed: {e}")
                            continue
                        ingest(drows)
                        time.sleep(REQUEST_PAUSE_S)

                logger.info(
                    f"Richland progress letter={letter} "
                    f"pager={has_pager} total={len(records)}"
                )
        except Exception as e:
            logger.error(f"Richland scrape failed: {e}")

        logger.info(
            f"Richland: {len(records)} unique inmates in {time.time() - start:.1f}s"
        )
        return records

    def _search_query(
        self, session: requests.Session, query: str
    ) -> tuple[List[dict], bool]:
        """Return (rows, has_pager). Fresh GET per query for valid captcha token."""
        resp = session.get(PORTAL_URL, timeout=25, verify=False)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        data = self._all_fields(soup)
        rand = data.get("ctl00$cphMain$hidStrRandom", "")
        if not data.get("__VIEWSTATE") or not rand:
            raise RuntimeError("missing ViewState or captcha token")

        data["ctl00$cphMain$txtLastName"] = query
        data["ctl00$cphMain$txtFirstName"] = ""
        data["ctl00$cphMain$txtBitMapCaptcha"] = rand  # captcha == random token
        data["ctl00$cphMain$btnSearch"] = "Search Offenders"
        data["__EVENTTARGET"] = ""
        data["__EVENTARGUMENT"] = ""

        resp = session.post(PORTAL_URL, data=data, timeout=35, verify=False)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        has_pager = "Page$" in resp.text
        return self._parse_grid(soup), has_pager

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
        grid = soup.find("table", id="ctl00_cphMain_gvMain")
        if not grid:
            return []
        out: List[dict] = []
        for tr in grid.find_all("tr")[1:]:
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 5:
                continue
            # Select | FullName | Age | Ht | Wt | Booked
            if cells[0].strip().lower() in ("select", ""):
                name, age, ht, wt = cells[1], cells[2], cells[3], cells[4]
                booked = cells[5] if len(cells) > 5 else ""
            else:
                if cells[0].isdigit() or not re.search(r"[A-Za-z]", cells[0]):
                    continue
                name = cells[0]
                age = cells[1] if len(cells) > 1 else ""
                ht = cells[2] if len(cells) > 2 else ""
                wt = cells[3] if len(cells) > 3 else ""
                booked = cells[4] if len(cells) > 4 else ""

            if not name or name.lower() in ("fullname", "select"):
                continue
            if not re.search(r"[A-Za-z]", name):
                continue
            out.append({
                "name": name,
                "age": age,
                "ht": ht,
                "wt": wt,
                "booked": booked,
            })
        return out

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
        booking_date = booked
        m = re.match(r"(\d{1,2}/\d{1,2}/\d{4})", booked)
        if m:
            booking_date = m.group(1)

        booking_num = (
            f"RIC_{re.sub(r'[^A-Za-z0-9]', '', last)[:10]}_"
            f"{re.sub(r'[^0-9]', '', booking_date)[:8]}_"
            f"{re.sub(r'[^0-9]', '', row.get('age') or '')}"
        )

        return ArrestRecord(
            County=self.county,
            State="SC",
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=booking_num,
            Booking_Date=booking_date,
            Arrest_Date=booking_date,
            Age_At_Arrest=str(row.get("age") or ""),
            Height=str(row.get("ht") or ""),
            Weight=str(row.get("wt") or ""),
            Charges="Unknown",  # list view only; detail postback is unstable
            Bond_Amount="0",
            Status="In Custody",
            Detail_URL=PORTAL_URL,
            Facility="Richland County Detention Center",
        )


RichlandScraper = RichlandSCScraper
