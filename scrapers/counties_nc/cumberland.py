"""
Cumberland County (NC) Arrest Scraper — CCSO Inmate Search.

Portal: https://www.ccsonc.org/inmate-search/
Alt:    P2C at https://p2c.ccsonc.org/p2c/jailinmates.aspx

Cumberland County (Fayetteville) is the 5th most populous county in NC.
Home to Fort Liberty (formerly Fort Bragg). High arrest volume.
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

PORTAL_URL = "https://www.ccsonc.org/inmate-search/"
P2C_URL = "https://p2c.ccsonc.org/p2c/jailinmates.aspx"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class CumberlandScraper(BaseScraper):
    """Cumberland County (NC) — CCSO inmate search."""

    @property
    def county(self) -> str:
        return "Cumberland"

    @property
    def state(self) -> str:
        return "NC"

    @property
    def scraper_id(self) -> str:
        return "scraper_nc_cumberland"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = requests.Session()
        session.headers.update(HEADERS)
        records: List[ArrestRecord] = []
        seen: set = set()

        # Strategy 1: Try P2C portal
        try:
            records = self._scrape_p2c(session)
        except Exception as e:
            logger.debug(f"Cumberland P2C failed: {e}")

        # Strategy 2: Try main portal
        if not records:
            try:
                records = self._scrape_portal(session)
            except Exception as e:
                logger.debug(f"Cumberland portal failed: {e}")

        # Strategy 3: DrissionPage fallback
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
        logger.info(f"✅ Cumberland (NC): {len(final)} records in {elapsed:.1f}s")
        return final

    def _scrape_p2c(self, session: requests.Session) -> List[ArrestRecord]:
        """Try the P2C portal with A-Z search."""
        records: List[ArrestRecord] = []

        resp = session.get(P2C_URL, timeout=30, allow_redirects=True)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        records = self._parse_table(soup)

        if not records:
            form = soup.find("form")
            if form:
                records = self._az_search(session, form, P2C_URL)

        return records

    def _scrape_portal(self, session: requests.Session) -> List[ArrestRecord]:
        """Try the main CCSO portal."""
        resp = session.get(PORTAL_URL, timeout=30, allow_redirects=True)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        return self._parse_table(soup)

    def _az_search(
        self, session: requests.Session, form, base_url: str
    ) -> List[ArrestRecord]:
        """Perform A-Z letter search on form."""
        all_records: List[ArrestRecord] = []
        seen: set = set()

        action = form.get("action", base_url)
        if action and not action.startswith("http"):
            action = base_url.rsplit("/", 1)[0] + "/" + action.lstrip("/")
        if not action:
            action = base_url

        hidden_fields = {}
        for inp in form.find_all("input", type="hidden"):
            name = inp.get("name", "")
            value = inp.get("value", "")
            if name:
                hidden_fields[name] = value

        for letter in string.ascii_uppercase:
            try:
                data = {**hidden_fields, "LastName": letter, "FirstName": ""}
                resp = session.post(action, data=data, timeout=30)
                if resp.status_code != 200:
                    continue
                batch = self._parse_table(BeautifulSoup(resp.text, "html.parser"))
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
        """DrissionPage fallback."""
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
                return self._parse_table(soup)
            return []
        except Exception as e:
            logger.debug(f"Cumberland browser fallback: {e}")
            return []

    def _parse_table(self, soup: BeautifulSoup) -> List[ArrestRecord]:
        """Parse inmate data from HTML tables."""
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
                if len(rows) < 3:
                    continue

            for row in rows[1:]:
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue

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

                if not name and cells:
                    name = cells[0]
                if not name or len(name) < 2:
                    continue

                if not booking_num:
                    booking_num = (
                        f"CUM_{hashlib.md5(f'{name}|CUMBERLAND_NC'.encode()).hexdigest()[:10]}"
                    )

                first, last = "", name
                if "," in name:
                    parts = name.split(",", 1)
                    last = parts[0].strip()
                    first = parts[1].strip()

                records.append(ArrestRecord(
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
                    Facility="Cumberland County Detention Center",
                ))

            if records:
                break

        return records
