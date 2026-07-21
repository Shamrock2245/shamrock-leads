"""
Mobile County (AL) Arrest Scraper.

Portal: https://all.mobileso.com/OthReports/CurrentInmates.aspx
(embedded via iframe at https://www.mobileso.com/whos-in-jail/)
Platform: Custom ASP.NET

Uses APE StealthSession (curl_cffi + residential failover) to bypass
datacenter 403 blocks observed 2026-07-20.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PORTAL_URL = "https://all.mobileso.com/OthReports/CurrentInmates.aspx"
FACILITY = "Mobile County Metro Jail"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.mobileso.com/whos-in-jail/",
}


class MobileScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Mobile"

    @property
    def state(self) -> str:
        return "AL"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []

        try:
            from scrapers.proxy_engine import create_stealth_session

            with create_stealth_session(
                sticky_session_id="al_mobile",
                prefer_residential=True,
                allow_direct=True,
            ) as session:
                resp = session.get(PORTAL_URL, headers=HEADERS, timeout=35)
                if resp.status_code != 200:
                    logger.warning(
                        "Mobile AL: HTTP %s — portal still blocked or down",
                        resp.status_code,
                    )
                    return []
                records = self._parse_html(resp.text)
        except Exception as exc:
            logger.error("Mobile AL scrape failed: %s", exc)
            return []

        logger.info(
            "✅ Mobile (AL): %d records in %.1fs",
            len(records),
            time.time() - start,
        )
        return records

    def _parse_html(self, html: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
        records: List[ArrestRecord] = []
        seen: set = set()

        # Prefer GridView / DataGrid tables with name + booking columns
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [
                th.get_text(" ", strip=True).lower()
                for th in rows[0].find_all(["th", "td"])
            ]
            joined = " ".join(headers)
            if not any(
                k in joined
                for k in ("name", "inmate", "booking", "arrest", "charge")
            ):
                continue

            name_i = self._col(headers, "name", "inmate")
            book_i = self._col(headers, "booking", "book #", "jacket", "so #")
            charge_i = self._col(headers, "charge", "offense")
            bond_i = self._col(headers, "bond", "bail")
            date_i = self._col(headers, "book date", "arrest date", "date")
            dob_i = self._col(headers, "dob", "birth")

            for row in rows[1:]:
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue
                name = cells[name_i] if name_i is not None and name_i < len(cells) else cells[0]
                if not name or len(name) < 3:
                    continue
                if name.lower() in ("name", "inmate name", "total"):
                    continue

                booking = (
                    cells[book_i]
                    if book_i is not None and book_i < len(cells)
                    else ""
                )
                if not booking:
                    booking = self._synthetic_booking(name, cells)

                if booking in seen:
                    continue
                seen.add(booking)

                charges = (
                    cells[charge_i]
                    if charge_i is not None and charge_i < len(cells)
                    else "Unknown"
                ) or "Unknown"
                bond = "0"
                if bond_i is not None and bond_i < len(cells):
                    bond = re.sub(r"[^\d.]", "", cells[bond_i]) or "0"
                arrest_date = (
                    cells[date_i]
                    if date_i is not None and date_i < len(cells)
                    else ""
                )
                dob = (
                    cells[dob_i]
                    if dob_i is not None and dob_i < len(cells)
                    else ""
                )
                first, last = self._split_name(name)

                records.append(
                    ArrestRecord(
                        County=self.county,
                        State="AL",
                        Full_Name=name.title() if name.isupper() else name,
                        First_Name=first,
                        Last_Name=last,
                        Booking_Number=str(booking),
                        DOB=dob,
                        Arrest_Date=arrest_date,
                        Booking_Date=arrest_date,
                        Charges=charges,
                        Bond_Amount=bond,
                        Status="In Custody",
                        Facility=FACILITY,
                        Agency="Mobile County Sheriff's Office",
                        Detail_URL=PORTAL_URL,
                    )
                )

            if records:
                break

        if not records:
            # Fail closed gracefully — selectors may have changed
            logger.warning(
                "Mobile AL: no inmate rows parsed (selector change or empty roster)"
            )
        return records

    @staticmethod
    def _col(headers: List[str], *keys: str) -> Optional[int]:
        for i, h in enumerate(headers):
            for k in keys:
                if k in h:
                    return i
        return None

    @staticmethod
    def _synthetic_booking(name: str, cells: List[str]) -> str:
        raw = name + "|" + "|".join(cells[:4])
        digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"MOB_{digest}"

    @staticmethod
    def _split_name(name: str) -> Tuple[str, str]:
        if "," in name:
            last, rest = name.split(",", 1)
            first = rest.strip().split()[0] if rest.strip() else ""
            return first.title(), last.strip().title()
        bits = name.split()
        if len(bits) >= 2:
            return bits[0].title(), bits[-1].title()
        return name.title(), ""
