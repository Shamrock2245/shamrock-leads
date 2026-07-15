"""
Davidson County (TN) Arrest Scraper — Nashville DCSO Active Inmate Search.
URL: https://dcso.nashville.gov
Platform: HTML / possible JS rendering.

Davidson County (Nashville) is TN's 2nd largest county (~700K).
The roster is updated hourly from the Davidson County Correctional Center.
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
PORTAL_URL = "https://dcso.nashville.gov"
INMATE_SEARCH_URL = "https://dcso.nashville.gov/inmates/search"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class DavidsonScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Davidson"

    @property
    def state(self) -> str:
        return "TN"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []
        session = requests.Session()
        session.headers.update(HEADERS)

        # Try different known URL patterns for Nashville DCSO
        urls_to_try = [
            INMATE_SEARCH_URL,
            f"{PORTAL_URL}/inmates",
            f"{PORTAL_URL}/InmateSearch",
            f"{PORTAL_URL}/jailroster",
        ]

        for url in urls_to_try:
            try:
                resp = session.get(url, timeout=25, verify=False, allow_redirects=True)
                if resp.status_code != 200:
                    continue

                # Check for JSON API response
                ctype = resp.headers.get("Content-Type", "")
                if "json" in ctype or resp.text.strip().startswith(("[", "{")):
                    try:
                        data = resp.json()
                        records = self._parse_json(data, url)
                        if records:
                            break
                    except Exception:
                        pass

                soup = BeautifulSoup(resp.text, "html.parser")
                records = self._parse_html(soup, url)
                if records:
                    break

            except Exception as e:
                logger.debug(f"Davidson {url}: {e}")

        if not records:
            # DrissionPage fallback for JS-rendered pages
            records = self._scrape_with_browser()

        logger.info(f"✅ Davidson (TN): {len(records)} records in {time.time()-start:.1f}s")
        return records

    def _parse_json(self, data, source_url: str) -> List[ArrestRecord]:
        """Parse JSON inmate data (common API format)."""
        records: List[ArrestRecord] = []
        inmates = []

        if isinstance(data, list):
            inmates = data
        elif isinstance(data, dict):
            for key in ("data", "inmates", "bookings", "results", "items", "records"):
                if key in data and isinstance(data[key], list):
                    inmates = data[key]
                    break

        for row in inmates:
            if not isinstance(row, dict):
                continue
            name = (
                row.get("name") or row.get("fullName") or row.get("full_name")
                or f"{row.get('lastName', row.get('last_name', ''))}, "
                   f"{row.get('firstName', row.get('first_name', ''))}".strip(", ")
            )
            if not name or name.strip() in (",", ""):
                continue

            booking = str(
                row.get("bookingNumber") or row.get("booking_number")
                or row.get("id") or f"DAV_{int(time.time())}"
            )
            charges = row.get("charges") or row.get("offense") or "Unknown"
            if isinstance(charges, list):
                charges = " | ".join(str(c) for c in charges)
            bond = str(row.get("bond") or row.get("bondAmount") or row.get("bond_amount") or "0")
            bond = re.sub(r"[^\d.]", "", bond) or "0"

            first = str(row.get("firstName", row.get("first_name", ""))).strip()
            last = str(row.get("lastName", row.get("last_name", ""))).strip()

            records.append(ArrestRecord(
                County=self.county,
                State="TN",
                Full_Name=str(name).strip(),
                First_Name=first,
                Last_Name=last,
                Booking_Number=booking,
                Booking_Date=str(row.get("bookingDate", row.get("booking_date", ""))),
                Charges=str(charges),
                Bond_Amount=bond,
                Status="In Custody",
                Detail_URL=source_url,
                Facility="Davidson County Correctional Center",
            ))

        return records

    def _parse_html(self, soup: BeautifulSoup, source_url: str) -> List[ArrestRecord]:
        """Parse HTML tables for inmate data."""
        records: List[ArrestRecord] = []

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [th.get_text(" ", strip=True).lower()
                       for th in rows[0].find_all(["th", "td"])]
            if not any(kw in " ".join(headers)
                       for kw in ("name", "inmate", "booking", "defendant")):
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

                for i, h in enumerate(headers):
                    if i >= len(cells):
                        break
                    if "book" in h and "date" not in h:
                        booking_num = cells[i]
                    elif "charge" in h or "offense" in h:
                        charges = cells[i]
                    elif "bond" in h or "bail" in h:
                        bond = re.sub(r"[^\d.]", "", cells[i]) or "0"

                if not booking_num:
                    booking_num = f"DAV_{hashlib.md5(f'{name}|DAVIDSON_TN'.encode()).hexdigest()[:10]}"

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
                    Charges=charges or "Unknown",
                    Bond_Amount=bond,
                    Status="In Custody",
                    Detail_URL=source_url,
                    Facility="Davidson County Correctional Center",
                ))
            if records:
                break

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
            soup = BeautifulSoup(html, "html.parser")
            records = self._parse_html(soup, PORTAL_URL)
            try:
                page.quit()
            except Exception:
                pass
            return records
        except Exception as e:
            logger.debug(f"Davidson browser fallback failed: {e}")
            return []
