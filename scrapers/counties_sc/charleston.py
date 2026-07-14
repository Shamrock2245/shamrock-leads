"""
Charleston County (SC) Arrest Scraper.
Platform: Custom ASP.NET ViewState form
URL: https://inmatesearch.charlestoncounty.gov/
Approach: POST with ViewState + date range for last 7 days
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
PORTAL_URL = "https://inmatesearch.charlestoncounty.gov/"


class CharlestonScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Charleston"

    @property
    def state(self) -> str:
        return "SC"

    def _get_viewstate(self, session: requests.Session) -> dict:
        resp = session.get(PORTAL_URL, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        fields = {}
        for name in ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"]:
            tag = soup.find("input", {"name": name})
            if tag:
                fields[name] = tag.get("value", "")
        return fields

    def scrape(self) -> List[ArrestRecord]:
        records: List[ArrestRecord] = []
        start_time = time.time()
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": PORTAL_URL,
            "Origin": "https://inmatesearch.charlestoncounty.gov",
        })

        try:
            vs = self._get_viewstate(session)
            if not vs.get("__VIEWSTATE"):
                logger.error("Charleston: Failed to extract ViewState")
                return []

            now = datetime.now()
            start = now - timedelta(days=7)
            date_fmt = "%m/%d/%Y"

            payload = {
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                "__VIEWSTATE": vs["__VIEWSTATE"],
                "__VIEWSTATEGENERATOR": vs.get("__VIEWSTATEGENERATOR", ""),
                "__EVENTVALIDATION": vs.get("__EVENTVALIDATION", ""),
                "ctl00$MainContent$txtLastName": "",
                "ctl00$MainContent$txtFirstName": "",
                "ctl00$MainContent$txtBookDtFrom": start.strftime(date_fmt),
                "ctl00$MainContent$txtBookDtTo": now.strftime(date_fmt),
                "ctl00$MainContent$txtArrestDtFrom": "",
                "ctl00$MainContent$txtArrestDtTo": "",
                "ctl00$MainContent$chkSoundex": "on",
                "ctl00$MainContent$txtInmateNumber": "",
                "ctl00$MainContent$btnSearch": "Search",
            }

            resp = session.post(PORTAL_URL, data=payload, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            table = soup.find("table", id=re.compile(r"GridView|gvResults|grdInmates", re.I))
            if not table:
                for t in soup.find_all("table"):
                    headers = [th.get_text(strip=True).lower() for th in t.find_all("th")]
                    if any(h in headers for h in ("name", "last name", "inmate", "booking")):
                        table = t
                        break

            if not table:
                logger.warning("Charleston: No results table found")
                return []

            headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("tr")[0].find_all(["th", "td"])]
            for row in table.find_all("tr")[1:]:
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue
                try:
                    # Prefer header mapping; fall back to positional
                    def col(*keys, default=""):
                        for i, h in enumerate(headers):
                            if any(k in h for k in keys) and i < len(cells):
                                return cells[i]
                        return default

                    last = col("last") or (cells[0] if cells else "")
                    first = col("first") or (cells[1] if len(cells) > 1 else "")
                    if "last" not in " ".join(headers) and len(cells) >= 2:
                        # some layouts are "Last, First" in one cell
                        if "," in cells[0] and not first:
                            last, first = [p.strip() for p in cells[0].split(",", 1)]
                        full_name = f"{last}, {first}".strip(", ")
                    else:
                        full_name = f"{last}, {first}".strip(", ") if last or first else cells[0]

                    booking_num = col("booking", "inmate #", "number") or (cells[2] if len(cells) > 2 else "")
                    booking_date_str = col("book", "date") or (cells[3] if len(cells) > 3 else "")
                    charge = col("charge", "offense") or (cells[4] if len(cells) > 4 else "Unknown")
                    bond_str = col("bond", "bail") or (cells[5] if len(cells) > 5 else "0")

                    if not booking_num:
                        booking_num = f"CHS_{re.sub(r'[^A-Za-z0-9]', '', full_name)[:16]}_{abs(hash(full_name + booking_date_str)) % 100000}"

                    bond = re.sub(r"[^\d.]", "", bond_str) or "0"
                    records.append(
                        ArrestRecord(
                            County=self.county,
                            State="SC",
                            Full_Name=full_name.title(),
                            First_Name=first.title() if first else "",
                            Last_Name=last.title() if last else "",
                            Booking_Number=str(booking_num),
                            Booking_Date=booking_date_str,
                            Charges=charge or "Unknown",
                            Bond_Amount=bond,
                            Status="In Custody",
                            Detail_URL=PORTAL_URL,
                        )
                    )
                except Exception as row_err:
                    logger.debug(f"Charleston row error: {row_err}")
        except Exception as e:
            logger.error(f"Charleston scrape failed: {e}")

        logger.info(f"Charleston: {len(records)} records in {time.time() - start_time:.1f}s")
        return records
