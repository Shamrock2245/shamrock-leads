"""
Wake County (NC) Arrest Scraper — Wake County Detention Center.

Portal: https://www.wcso-nc.us/inmate-search/
Alt:    P2C at https://p2c.wakeso.net/p2c/jailinmates.aspx (if available)

Wake County (Raleigh) is the most populous county in NC.
The Sheriff's Office provides an inmate search portal.
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

PORTAL_URL = "https://www.wcso-nc.us/inmate-search/"
P2C_URL = "https://p2c.wakeso.net/p2c/jailinmates.aspx"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class WakeScraper(BaseScraper):
    """Wake County (NC) — Detention Center inmate roster."""

    @property
    def county(self) -> str:
        return "Wake"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def scraper_id(self) -> str:
        return "scraper_nc_wake"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(HEADERS)
        records: List[ArrestRecord] = []
        seen: set = set()

        # Strategy 1: Try P2C portal (standard CentralSquare format)
        try:
            records = self._scrape_p2c(session)
        except Exception as e:
            logger.debug(f"Wake P2C failed: {e}")

        # Strategy 2: Try the WCSO portal with letter walk
        if not records:
            try:
                records = self._scrape_wcso(session)
            except Exception as e:
                logger.debug(f"Wake WCSO failed: {e}")

        # Strategy 3: DrissionPage fallback
        if not records:
            records = self._scrape_with_browser()

        # Dedup
        final: List[ArrestRecord] = []
        for rec in records:
            key = rec.Booking_Number or rec.Full_Name
            if key not in seen:
                seen.add(key)
                final.append(rec)

        elapsed = time.time() - start
        logger.info(f"✅ Wake (NC): {len(final)} records in {elapsed:.1f}s")
        return final

    def _scrape_p2c(self, session: requests.Session) -> List[ArrestRecord]:
        """Try the P2C (Police-to-Citizen) portal."""
        records: List[ArrestRecord] = []

        resp = session.get(P2C_URL, timeout=30, allow_redirects=True)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        records = self._parse_p2c_table(soup)

        # If no results on landing, try A-Z search
        if not records:
            form = soup.find("form")
            if form:
                records = self._az_search_p2c(session, soup)

        return records

    def _az_search_p2c(
        self, session: requests.Session, initial_soup: BeautifulSoup
    ) -> List[ArrestRecord]:
        """Perform A-Z letter search on P2C form."""
        all_records: List[ArrestRecord] = []
        form = initial_soup.find("form")
        if not form:
            return []

        action = form.get("action", P2C_URL)
        if not action.startswith("http"):
            action = P2C_URL.rsplit("/", 1)[0] + "/" + action.lstrip("/")

        # Collect hidden fields
        hidden_fields = {}
        for inp in form.find_all("input", type="hidden"):
            name = inp.get("name", "")
            value = inp.get("value", "")
            if name:
                hidden_fields[name] = value

        seen = set()
        for letter in string.ascii_uppercase:
            try:
                data = {**hidden_fields, "LastName": letter, "FirstName": ""}
                resp = session.post(action, data=data, timeout=30)
                if resp.status_code != 200:
                    continue
                batch = self._parse_p2c_table(BeautifulSoup(resp.text, "html.parser"))
                for rec in batch:
                    key = rec.Booking_Number or rec.Full_Name
                    if key not in seen:
                        seen.add(key)
                        all_records.append(rec)
            except Exception:
                pass
            time.sleep(0.3)

        return all_records

    def _scrape_wcso(self, session: requests.Session) -> List[ArrestRecord]:
        """Try the WCSO inmate search portal."""
        records: List[ArrestRecord] = []

        resp = session.get(PORTAL_URL, timeout=30, allow_redirects=True)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        records = self._parse_generic_table(soup, PORTAL_URL)
        return records

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
                return self._parse_generic_table(soup, PORTAL_URL)
            return []
        except Exception as e:
            logger.debug(f"Wake browser fallback failed: {e}")
            return []

    # ── Parsers ──────────────────────────────────────────────────────────────

    def _parse_p2c_table(self, soup: BeautifulSoup) -> List[ArrestRecord]:
        """Parse standard P2C jail inmates table."""
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
                for kw in ("name", "inmate", "booking", "defendant")
            ):
                continue

            for row in rows[1:]:
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(cells) < 3:
                    continue

                rec = self._cells_to_record(cells, headers)
                if rec:
                    records.append(rec)

            if records:
                break

        return records

    def _parse_generic_table(
        self, soup: BeautifulSoup, source_url: str
    ) -> List[ArrestRecord]:
        """Parse any table that looks like an inmate roster."""
        records: List[ArrestRecord] = []

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue

            headers = [
                th.get_text(" ", strip=True).lower()
                for th in rows[0].find_all(["th", "td"])
            ]

            for row in rows[1:]:
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue

                rec = self._cells_to_record(cells, headers)
                if rec:
                    records.append(rec)

            if records:
                break

        return records

    def _cells_to_record(
        self, cells: List[str], headers: List[str]
    ) -> ArrestRecord | None:
        """Convert table cells to ArrestRecord using header mapping."""
        name = ""
        booking_num = ""
        charges = "Unknown"
        bond = "0"
        booking_date = ""

        for i, h in enumerate(headers):
            if i >= len(cells):
                break
            val = cells[i].strip()
            if not val:
                continue

            if any(kw in h for kw in ("name", "inmate", "defendant")):
                name = val
            elif "book" in h and "date" not in h:
                booking_num = val
            elif "book" in h and "date" in h:
                booking_date = val
            elif "charge" in h or "offense" in h:
                charges = val
            elif "bond" in h or "bail" in h:
                bond = re.sub(r"[^\d.]", "", val) or "0"

        # If no header match, try positional
        if not name and cells:
            name = cells[0]

        if not name or len(name) < 2:
            return None

        if not booking_num:
            booking_num = (
                f"WAK_{hashlib.md5(f'{name}|WAKE_NC'.encode()).hexdigest()[:10]}"
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
            Booking_Number=str(booking_num),
            Booking_Date=booking_date,
            Charges=charges or "Unknown",
            Bond_Amount=bond,
            Status="In Custody",
            Detail_URL=PORTAL_URL,
            Facility="Wake County Detention Center",
        )
