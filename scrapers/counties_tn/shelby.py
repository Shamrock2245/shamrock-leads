"""
Shelby County (TN) Arrest Scraper — Memphis / Shelby County Jail Roster.
URL: https://www.shelbycountytn.gov/691/Corrections-Division
Approach: Scrape the public jail roster page via requests + BeautifulSoup.

Shelby County (Memphis) is the most populous county in TN (~930K).
The jail roster is served from the Sheriff's Office site.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import List

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)
# Primary jail roster endpoint
PORTAL_URL = "https://www.shelbycountytn.gov/691/Corrections-Division"
# Alternate direct inmate search (some counties expose a separate search)
ALT_ROSTER_URL = "https://jis.shelbycountytn.gov/JISWeb/jailRoster"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class ShelbyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Shelby"

    @property
    def state(self) -> str:
        return "TN"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []

        # Try the alternate JIS roster endpoint first (more structured)
        records = self._scrape_jis_roster()
        if records:
            logger.info(f"✅ Shelby (TN): {len(records)} records via JIS in {time.time()-start:.1f}s")
            return records

        # Fallback to main portal page scrape
        records = self._scrape_portal()
        logger.info(f"✅ Shelby (TN): {len(records)} records in {time.time()-start:.1f}s")
        return records

    def _scrape_jis_roster(self) -> List[ArrestRecord]:
        """Try the Shelby County JIS web jail roster (HTML table)."""
        records: List[ArrestRecord] = []
        session = requests.Session()
        session.headers.update(HEADERS)

        try:
            resp = session.get(ALT_ROSTER_URL, timeout=30, verify=False)
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            records = self._parse_tables(soup, ALT_ROSTER_URL)
        except Exception as e:
            logger.debug(f"Shelby JIS roster failed: {e}")

        return records

    def _scrape_portal(self) -> List[ArrestRecord]:
        """Scrape the main Shelby County corrections page."""
        records: List[ArrestRecord] = []
        session = requests.Session()
        session.headers.update(HEADERS)

        try:
            resp = session.get(PORTAL_URL, timeout=30, verify=False)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Look for embedded iframes or links to actual roster
            for iframe in soup.find_all("iframe"):
                src = iframe.get("src", "")
                if "roster" in src.lower() or "inmate" in src.lower() or "jail" in src.lower():
                    try:
                        resp2 = session.get(src, timeout=30, verify=False)
                        soup2 = BeautifulSoup(resp2.text, "html.parser")
                        records = self._parse_tables(soup2, src)
                        if records:
                            return records
                    except Exception:
                        pass

            # Try parsing the page itself
            records = self._parse_tables(soup, PORTAL_URL)

        except Exception as e:
            logger.error(f"Shelby portal scrape failed: {e}")

        return records

    def _parse_tables(self, soup: BeautifulSoup, source_url: str) -> List[ArrestRecord]:
        """Parse inmate data from any table found on the page."""
        records: List[ArrestRecord] = []

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue

            header_row = rows[0]
            headers = [th.get_text(" ", strip=True).lower()
                       for th in header_row.find_all(["th", "td"])]

            # Verify this is an inmate table
            if not any(kw in " ".join(headers) for kw in ("name", "inmate", "booking", "defendant")):
                if len(rows) < 3:
                    continue

            for row in rows[1:]:
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue

                name = cells[0]
                if not name or len(name) < 2:
                    continue

                booking_num = ""
                charges = "Unknown"
                bond = "0"
                booking_date = ""

                for i, h in enumerate(headers):
                    if i >= len(cells):
                        break
                    val = cells[i]
                    if "book" in h and "number" in h:
                        booking_num = val
                    elif "book" in h and "date" in h:
                        booking_date = val
                    elif "charge" in h or "offense" in h:
                        charges = val
                    elif "bond" in h or "bail" in h:
                        bond = re.sub(r"[^\d.]", "", val) or "0"

                if not booking_num:
                    booking_num = f"SHE_{hashlib.md5(f'{name}|SHELBY_TN'.encode()).hexdigest()[:10]}"

                first, last = "", name
                if "," in name:
                    parts = name.split(",", 1)
                    last = parts[0].strip()
                    first = parts[1].strip()

                records.append(ArrestRecord(
                    County=self.county,
                    State="TN",
                    Full_Name=name.title(),
                    First_Name=first.title() if first else "",
                    Last_Name=last.title() if last else "",
                    Booking_Number=str(booking_num),
                    Booking_Date=booking_date,
                    Charges=charges or "Unknown",
                    Bond_Amount=bond,
                    Status="In Custody",
                    Detail_URL=source_url,
                    Facility="Shelby County Jail",
                ))

            if records:
                break

        return records
