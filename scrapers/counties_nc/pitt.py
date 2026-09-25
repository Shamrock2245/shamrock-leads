"""
Pitt County (NC) Arrest Scraper — Detainee Search ASP.NET GridView (plain HTTPS).

URL: https://apps.pittcountync.gov/apps/detention/detainee/

Source contract (verified 2026-09-25 from datacenter egress, plain requests):

* A **blank** "Get Detainee" search returns every current detainee in a
  10-row GridView (``ctl00_mainContent_GridView1``) with columns
  Last Name / Suffix / First Name / Middle Name / Date of Birth /
  **Booking Number** / Gender / Race.
* Later pages are ordinary GridView postbacks
  (``__EVENTTARGET=ctl00$mainContent$GridView1``, ``__EVENTARGUMENT=Page$N``)
  carrying the previous response's ``__VIEWSTATE``/``__EVENTVALIDATION``.
  ~48 pages / ~480 detainees on 2026-09-25.
* ``Booking Number`` (digits) is the source booking key. Rows without one
  are skipped; nothing is synthesized.

The previous implementation ran an A–Z / digraph last-name walk and never
followed the pager, so every saturated search was cut at 10 rows.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper
from scrapers.scraper_resilience import ParseDriftError

logger = logging.getLogger(__name__)

PORTAL_URL = "https://apps.pittcountync.gov/apps/detention/detainee/"
GRID_ID = "ctl00_mainContent_GridView1"
GRID_TARGET = "ctl00$mainContent$GridView1"
BOOKING_RE = re.compile(r"^\d{4,10}$")
EXPECTED_HEADERS = ["Last Name", "First Name", "Date of Birth", "Booking Number"]


def form_fields(soup: BeautifulSoup) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for inp in soup.find_all("input"):
        name = inp.get("name")
        typ = (inp.get("type") or "text").lower()
        if not name or typ in ("submit", "button", "image", "checkbox", "radio"):
            continue
        data[name] = inp.get("value") or ""
    return data


def parse_grid(soup: BeautifulSoup) -> List[dict]:
    """Parse one GridView page. Raises ParseDriftError if headers moved."""
    table = soup.find("table", id=GRID_ID)
    if table is None:
        return []
    trs = table.find_all("tr")
    if not trs:
        return []
    headers = [c.get_text(" ", strip=True) for c in trs[0].find_all(["th", "td"])]
    if any(h not in headers for h in EXPECTED_HEADERS):
        raise ParseDriftError(f"Pitt: GridView headers changed: {headers[:10]}")
    idx = {h: i for i, h in enumerate(headers)}
    out: List[dict] = []
    for tr in trs[1:]:
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < len(EXPECTED_HEADERS) or not tr.find("a", string="Select"):
            continue  # pager row / spacer

        def cell(label: str) -> str:
            i = idx.get(label)
            return cells[i].strip() if i is not None and i < len(cells) else ""

        booking = cell("Booking Number")
        last = cell("Last Name")
        if not last or not BOOKING_RE.match(booking):
            continue
        out.append({
            "last": last,
            "suffix": cell("Suffix"),
            "first": cell("First Name"),
            "middle": cell("Middle Name"),
            "dob": cell("Date of Birth"),
            "booking": booking,
            "gender": cell("Gender"),
            "race": cell("Race"),
        })
    return out


def has_next_page(soup: BeautifulSoup, current: int) -> bool:
    """True when the pager offers ``Page$<current+1>`` (a number or the ``...`` link)."""
    pager = str(soup.find("table", id=GRID_ID) or "")
    return f"Page${current + 1}'" in pager


class PittScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "Pitt Detainee Search (plain HTTPS); blank search + GridView Page$N "
        "postbacks; source Booking Number column."
    )

    MAX_PAGES = 120  # ~48 used on 2026-09-25; hard stop against pager loops
    PAGE_DELAY_S = 0.25

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
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        })

        landing = session.get(PORTAL_URL, timeout=40)
        landing.raise_for_status()
        soup = BeautifulSoup(landing.text, "html.parser")
        data = form_fields(soup)
        if "ctl00$mainContent$lastNameTextBox" not in data:
            raise ParseDriftError("Pitt: detainee search form changed")
        data.update({
            "ctl00$mainContent$bookNumTextBox": "",
            "ctl00$mainContent$lastNameTextBox": "",
            "ctl00$mainContent$firstNameTextBox": "",
            "ctl00$mainContent$Button3": "Get  Detainee",
        })
        resp = session.post(PORTAL_URL, data=data, timeout=60)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        rows: Dict[str, dict] = {}
        for r in parse_grid(soup):
            rows.setdefault(r["booking"], r)
        page = 1
        while page < self.MAX_PAGES:
            if not has_next_page(soup, page):
                break
            page += 1
            post = form_fields(soup)
            post["__EVENTTARGET"] = GRID_TARGET
            post["__EVENTARGUMENT"] = f"Page${page}"
            time.sleep(self.PAGE_DELAY_S)
            resp = session.post(PORTAL_URL, data=post, timeout=60)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            page_rows = parse_grid(soup)
            if not page_rows:
                break
            new = 0
            for r in page_rows:
                if r["booking"] not in rows:
                    rows[r["booking"]] = r
                    new += 1
            if new == 0:
                break  # server clamped to the last page

        records = [self._to_record(r) for r in rows.values()]
        logger.info("Pitt: %d detainees over %d pages in %.1fs", len(records), page, time.time() - start)
        return records

    def _to_record(self, row: dict) -> ArrestRecord:
        last, first, middle = row["last"], row["first"], row["middle"]
        suffix = row.get("suffix") or ""
        given = " ".join(p for p in (first, middle, suffix) if p).strip()
        full = f"{last}, {given}".strip(", ")
        return ArrestRecord(
            County=self.county,
            State="NC",
            Full_Name=full,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=row["booking"],
            DOB=row.get("dob") or "",
            Sex=(row.get("gender") or "")[:1].upper(),
            Race=row.get("race") or "",
            Charges="Unknown",
            Bond_Amount="0",
            Status="In Custody",
            Detail_URL=PORTAL_URL,
            Facility="Pitt County Detention Center",
        )
