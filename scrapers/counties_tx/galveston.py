"""
Galveston County (TX) Arrest Scraper — P2C / CentralSquare jqGrid.

Portal: https://p2c.galvestoncountytx.gov/jailinmates.aspx
API:    POST jqHandler.ashx?op=s  postData { t: 'ii' }  (rows=50, paginate)

Classic ASP.NET P2C with client-side jqGrid — list HTML is empty until the
JSON endpoint is POSTed with t=ii. rows must stay modest (~50); larger page
sizes return empty result sets.
"""
from __future__ import annotations

import logging
import re
import time
from typing import List
from urllib.parse import urljoin

import requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

PORTAL_URL = "https://p2c.galvestoncountytx.gov/jailinmates.aspx"
API_URL = "https://p2c.galvestoncountytx.gov/jqHandler.ashx?op=s"
FACILITY = "Galveston County Jail"
PAGE_SIZE = 50  # larger sizes return empty rows on this host
MAX_PAGES = 80  # safety cap (~4k inmates)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class GalvestonScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Galveston"

    @property
    def state(self) -> str:
        return "TX"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(HEADERS)

        try:
            # Session cookie + ASP.NET state from landing page
            land = session.get(PORTAL_URL, timeout=40)
            land.raise_for_status()
        except Exception as e:
            logger.error(f"Galveston: portal GET failed: {e}")
            return []

        ajax_headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": PORTAL_URL,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

        records: List[ArrestRecord] = []
        seen: set = set()
        page = 1
        total_pages = 1

        while page <= total_pages and page <= MAX_PAGES:
            payload = {
                "_search": "false",
                "nd": str(int(time.time() * 1000)),
                "rows": str(PAGE_SIZE),
                "page": str(page),
                "sidx": "",
                "sord": "asc",
                "t": "ii",
            }
            try:
                resp = session.post(API_URL, data=payload, headers=ajax_headers, timeout=40)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.warning(f"Galveston jqGrid page {page}: {e}")
                break

            try:
                total_pages = max(1, int(data.get("total") or 1))
            except (TypeError, ValueError):
                total_pages = 1

            rows = data.get("rows") or []
            if not rows:
                if page == 1:
                    logger.error(
                        "Galveston: jqGrid returned 0 rows (check t=ii / page size)"
                    )
                break

            for row in rows:
                rec = self._row_to_record(row)
                if not rec:
                    continue
                key = rec.Booking_Number or rec.Full_Name
                if not key or key in seen:
                    continue
                seen.add(key)
                records.append(rec)

            logger.debug(
                f"Galveston page {page}/{total_pages}: +{len(rows)} "
                f"(running {len(records)})"
            )
            page += 1
            time.sleep(0.25)

        logger.info(
            f"✅ Galveston (TX): {len(records)} records in {time.time() - start:.1f}s"
        )
        return records

    def _row_to_record(self, row: dict) -> ArrestRecord | None:
        if not isinstance(row, dict):
            return None

        last = (row.get("lastname") or "").strip()
        first = (row.get("firstname") or "").strip()
        middle = (row.get("middlename") or "").strip()
        disp = (row.get("disp_name") or "").strip()

        # disp_name often "LAST, FIRST MIDDLE (R /S/age)"
        if disp and not last:
            clean = re.sub(r"\s*\([^)]*\)\s*$", "", disp).strip()
            first, middle, last = self._pn(clean)
        elif not last and not first:
            return None

        if last and first:
            name = f"{last}, {first}" + (f" {middle}" if middle else "")
        else:
            name = re.sub(r"\s*\([^)]*\)\s*$", "", disp).strip() or f"{first} {last}".strip()

        booking = str(
            row.get("book_id")
            or row.get("invid")
            or row.get("my_num")
            or ""
        ).strip()
        if not booking:
            booking = f"GAL_{re.sub(r'[^A-Za-z0-9]', '', name)[:16]}"

        charges = (
            row.get("disp_charge")
            or row.get("chrgdesc")
            or "Unknown"
        )
        if isinstance(charges, str):
            charges = charges.strip() or "Unknown"

        book_date = (
            row.get("disp_arrest_date")
            or row.get("date_arr")
            or ""
        )
        if book_date:
            # normalize "7/8/2026 12:00:00 AM" → date portion
            book_date = str(book_date).split()[0]

        dob = row.get("dob") or ""
        if dob:
            dob = str(dob).split()[0]

        sex = (row.get("sex") or "").strip()
        race = (row.get("race") or "").strip()

        detail = ""
        link_text = row.get("link_text") or ""
        if isinstance(link_text, str) and link_text.startswith("http"):
            detail = link_text
        elif isinstance(link_text, str) and link_text:
            detail = urljoin(PORTAL_URL, link_text)

        return ArrestRecord(
            County=self.county,
            State="TX",
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=booking,
            Booking_Date=str(book_date),
            DOB=str(dob),
            Sex=sex,
            Race=race,
            Charges=str(charges),
            Bond_Amount="0",
            Status="In Custody",
            Facility=FACILITY,
            Detail_URL=detail or PORTAL_URL,
            LastCheckedMode="INITIAL",
        )

    @staticmethod
    def _pn(n: str):
        n = " ".join((n or "").strip().split())
        if "," in n:
            last, rest = n.split(",", 1)
            p = rest.strip().split()
            return (p[0] if p else ""), (" ".join(p[1:]) if len(p) > 1 else ""), last.strip()
        p = n.split()
        return (
            (p[0] if p else ""),
            (" ".join(p[1:-1]) if len(p) > 2 else ""),
            (p[-1] if len(p) > 1 else n),
        )
