"""
Orleans Parish (LA) Arrest Scraper — OPSO / Beacon JMS Portal.
URL: https://www.opso.gov  (Beacon JMS)
Alt: https://Orleans.LAVINE.org (LA VINE public roster)

Orleans Parish (New Orleans) is Louisiana's highest-volume parish.
OPSO transitioned to Beacon JMS for inmate records.
We try the OPSO detainee search first, then fall back to LAVINE.
Note: LA uses "Parish" instead of "County" — we store as "Orleans"
      with Parish suffix in the Facility field for clarity.
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
OPSO_URL = "https://www.opso.gov"
LAVINE_URL = "https://www.vinelink.com/vinelink/initMap.do"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class OrleansScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Orleans"

    @property
    def state(self) -> str:
        return "LA"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []

        # Strategy 1: Try the OPSO Beacon JMS portal
        records = self._scrape_opso()

        # Strategy 2: Fall back to DrissionPage if the portal is JS-rendered
        if not records:
            records = self._scrape_with_browser()

        logger.info(f"✅ Orleans (LA): {len(records)} records in {time.time()-start:.1f}s")
        return records

    def _scrape_opso(self) -> List[ArrestRecord]:
        """Scrape OPSO Beacon JMS portal for detainee data."""
        records: List[ArrestRecord] = []
        session = requests.Session()
        session.headers.update(HEADERS)

        urls = [
            f"{OPSO_URL}/inmates",
            f"{OPSO_URL}/detainee-search",
            f"{OPSO_URL}/jail-roster",
            f"{OPSO_URL}/beacon/inmates",
        ]

        for url in urls:
            try:
                resp = session.get(url, timeout=25, verify=False, allow_redirects=True)
                if resp.status_code != 200:
                    continue

                ctype = resp.headers.get("Content-Type", "")
                if "json" in ctype or resp.text.strip().startswith(("[", "{")):
                    try:
                        data = resp.json()
                        records = self._parse_json(data, url)
                        if records:
                            return records
                    except Exception:
                        pass

                soup = BeautifulSoup(resp.text, "html.parser")
                records = self._parse_html(soup, url)
                if records:
                    return records

            except Exception as e:
                logger.debug(f"Orleans OPSO {url}: {e}")

        return records

    def _parse_json(self, data, source_url: str) -> List[ArrestRecord]:
        """Parse JSON inmate data."""
        records: List[ArrestRecord] = []
        inmates = []
        if isinstance(data, list):
            inmates = data
        elif isinstance(data, dict):
            for key in ("data", "inmates", "bookings", "results", "detainees", "items"):
                if key in data and isinstance(data[key], list):
                    inmates = data[key]
                    break

        for row in inmates:
            if not isinstance(row, dict):
                continue
            name = (
                row.get("name") or row.get("fullName") or row.get("full_name")
                or f"{row.get('lastName', '')}, {row.get('firstName', '')}".strip(", ")
            )
            if not name or name.strip() in (",", ""):
                continue

            booking = str(
                row.get("bookingNumber") or row.get("booking_number")
                or row.get("item_no") or row.get("id")
                or f"ORL_{int(time.time())}"
            )
            charges = row.get("charges") or row.get("offense") or "Unknown"
            if isinstance(charges, list):
                charges = " | ".join(str(c.get("description", c) if isinstance(c, dict) else c) for c in charges)
            bond = str(row.get("bond") or row.get("bondAmount") or "0")
            bond = re.sub(r"[^\d.]", "", bond) or "0"

            records.append(ArrestRecord(
                County=self.county,
                State="LA",
                Full_Name=str(name).strip(),
                First_Name=str(row.get("firstName", row.get("first_name", ""))).strip(),
                Last_Name=str(row.get("lastName", row.get("last_name", ""))).strip(),
                Booking_Number=booking,
                Booking_Date=str(row.get("bookingDate", row.get("booking_date", ""))),
                Charges=str(charges),
                Bond_Amount=bond,
                Status="In Custody",
                Detail_URL=source_url,
                Facility="Orleans Parish Prison (OPP)",
            ))
        return records

    def _parse_html(self, soup: BeautifulSoup, source_url: str) -> List[ArrestRecord]:
        """Parse HTML tables for inmate data."""
        records: List[ArrestRecord] = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [th.get_text(" ", strip=True).lower() for th in rows[0].find_all(["th", "td"])]
            if not any(kw in " ".join(headers) for kw in ("name", "inmate", "booking", "detainee")):
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
                    booking_num = f"ORL_{hashlib.md5(f'{name}|ORLEANS_LA'.encode()).hexdigest()[:10]}"

                first, last = "", name
                if "," in name:
                    parts = name.split(",", 1)
                    last = parts[0].strip()
                    first = parts[1].strip()

                records.append(ArrestRecord(
                    County=self.county,
                    State="LA",
                    Full_Name=name.title(),
                    First_Name=first.title() if first else "",
                    Last_Name=last.title() if last else "",
                    Booking_Number=str(booking_num),
                    Charges=charges or "Unknown",
                    Bond_Amount=bond,
                    Status="In Custody",
                    Detail_URL=source_url,
                    Facility="Orleans Parish Prison (OPP)",
                ))
            if records:
                break
        return records

    def _scrape_with_browser(self) -> List[ArrestRecord]:
        """DrissionPage fallback for JS-rendered OPSO content."""
        try:
            from DrissionPage import ChromiumPage
            co = self._get_browser_options()
            page = ChromiumPage(co)
            page.get(f"{OPSO_URL}")
            page.wait.doc_loaded()
            time.sleep(3)

            # Navigate to detainee search if link exists
            for link_text in ("Detainee", "Inmate", "Jail", "Search"):
                try:
                    link = page.ele(f'xpath://a[contains(text(), "{link_text}")]')
                    if link:
                        link.click()
                        time.sleep(2)
                        break
                except Exception:
                    pass

            html = page.html
            soup = BeautifulSoup(html, "html.parser")
            records = self._parse_html(soup, OPSO_URL)
            try:
                page.quit()
            except Exception:
                pass
            return records
        except Exception as e:
            logger.debug(f"Orleans browser fallback failed: {e}")
            return []
