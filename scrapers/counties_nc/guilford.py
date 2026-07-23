"""
Guilford County (NC) Arrest Scraper — GCSO Inmate Lookup.

Portal: https://www.guilfordcountysheriff.com/inmate-lookup/
Alt:    Odyssey portal may be available for court records.

Guilford County (Greensboro / High Point) is the 3rd most populous
county in NC. The Sheriff provides an online inmate lookup.
"""
from __future__ import annotations

import hashlib
import logging
import re
import string
import time
from typing import List

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

PORTAL_URL = "https://www.guilfordcountysheriff.com/inmate-lookup/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class GuilfordScraper(BaseScraper):
    """Guilford County (NC) — GCSO inmate lookup."""

    @property
    def county(self) -> str:
        return "Guilford"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def scraper_id(self) -> str:
        return "scraper_nc_guilford"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        seen: set = set()

        # Primary: requests-based fetch
        session = requests.Session()
        session.headers.update(HEADERS)

        try:
            records = self._scrape_portal(session)
        except Exception as e:
            logger.debug(f"Guilford portal failed: {e}")

        # Fallback: DrissionPage browser
        if not records:
            records = self._scrape_with_browser()

        # Dedup
        final: List[ArrestRecord] = []
        for rec in records:
            key = rec.Booking_Number
            if key not in seen:
                seen.add(key)
                final.append(rec)

        elapsed = time.time() - start
        logger.info(f"✅ Guilford (NC): {len(final)} records in {elapsed:.1f}s")
        return final

    def _scrape_portal(self, session: requests.Session) -> List[ArrestRecord]:
        """Fetch and parse the inmate lookup portal."""
        records: List[ArrestRecord] = []

        resp = session.get(PORTAL_URL, timeout=30, allow_redirects=True)
        if resp.status_code != 200:
            logger.warning(f"Guilford portal: HTTP {resp.status_code}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")

        # Check if there's a search form requiring letter input
        form = soup.find("form")
        if form:
            records = self._az_search(session, soup)
        else:
            records = self._parse_roster(soup)

        return records

    def _az_search(
        self, session: requests.Session, initial_soup: BeautifulSoup
    ) -> List[ArrestRecord]:
        """Perform A-Z letter search if the portal requires name input."""
        all_records: List[ArrestRecord] = []
        seen: set = set()

        form = initial_soup.find("form")
        if not form:
            return []

        action = form.get("action", PORTAL_URL)
        if action and not action.startswith("http"):
            action = PORTAL_URL.rsplit("/", 1)[0] + "/" + action.lstrip("/")
        if not action:
            action = PORTAL_URL

        # Collect hidden fields
        hidden_fields = {}
        for inp in form.find_all("input", type="hidden"):
            name = inp.get("name", "")
            value = inp.get("value", "")
            if name:
                hidden_fields[name] = value

        # Find the last name input field name
        ln_input = form.find(
            "input",
            attrs={"name": re.compile(r"last|lname|surname", re.I)},
        )
        ln_field = ln_input.get("name") if ln_input else "LastName"

        for letter in string.ascii_uppercase:
            try:
                data = {**hidden_fields, ln_field: letter}
                resp = session.post(action, data=data, timeout=30)
                if resp.status_code != 200:
                    continue
                batch = self._parse_roster(BeautifulSoup(resp.text, "html.parser"))
                for rec in batch:
                    key = rec.Booking_Number
                    if key not in seen:
                        seen.add(key)
                        all_records.append(rec)
            except Exception:
                pass
            time.sleep(0.3)

        return all_records

    def _scrape_with_browser(self) -> List[ArrestRecord]:
        """DrissionPage fallback for JS-rendered content."""
        try:
            from DrissionPage import ChromiumPage

            co = self._get_browser_options()
            page = ChromiumPage(co)
            page.get(PORTAL_URL)
            page.wait.doc_loaded()
            time.sleep(3)

            html = page.html
            try:
                page.quit()
            except Exception:
                pass

            if html:
                soup = BeautifulSoup(html, "html.parser")
                return self._parse_roster(soup)
            return []
        except Exception as e:
            logger.debug(f"Guilford browser fallback: {e}")
            return []

    def _parse_roster(self, soup: BeautifulSoup) -> List[ArrestRecord]:
        """Parse inmate roster from HTML tables."""
        records: List[ArrestRecord] = []

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue

            headers = [
                th.get_text(" ", strip=True).lower()
                for th in rows[0].find_all(["th", "td"])
            ]

            if not any(
                kw in " ".join(headers)
                for kw in ("name", "inmate", "booking", "defendant", "offender")
            ):
                if len(rows) < 3:
                    continue

            for row in rows[1:]:
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue

                rec = self._row_to_record(cells, headers)
                if rec:
                    records.append(rec)

            if records:
                break

        return records

    def _row_to_record(
        self, cells: List[str], headers: List[str]
    ) -> ArrestRecord | None:
        """Convert a table row to an ArrestRecord."""
        name = ""
        booking_num = ""
        charges = "Unknown"
        bond = "0"
        booking_date = ""
        dob = ""

        for i, h in enumerate(headers):
            if i >= len(cells):
                break
            val = cells[i].strip()
            if not val:
                continue

            if any(kw in h for kw in ("name", "inmate", "defendant", "offender")):
                name = val
            elif "book" in h and "date" not in h:
                booking_num = val
            elif "book" in h and "date" in h:
                booking_date = val
            elif "charge" in h or "offense" in h:
                charges = val
            elif "bond" in h or "bail" in h:
                bond = re.sub(r"[^\d.]", "", val) or "0"
            elif "dob" in h or "birth" in h:
                dob = val

        if not name and cells:
            name = cells[0]

        if not name or len(name) < 2:
            return None

        if not booking_num:
            booking_num = (
                f"GLD_{hashlib.md5(f'{name}|GUILFORD_NC'.encode()).hexdigest()[:10]}"
            )

        first, last = "", name
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip()
            first = parts[1].strip()

        return ArrestRecord(
            County=self.county,
            State="NC",
            Full_Name=name.title() if name.isupper() else name,
            First_Name=first.title() if first else "",
            Last_Name=last.title() if last else "",
            DOB=dob,
            Booking_Number=str(booking_num),
            Booking_Date=booking_date,
            Charges=charges or "Unknown",
            Bond_Amount=bond,
            Status="In Custody",
            Detail_URL=PORTAL_URL,
            Facility="Guilford County Detention Center",
        )
