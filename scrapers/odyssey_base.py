"""
Base scraper for Tyler Odyssey / related jail portals.

GA counties labeled "Odyssey" use heterogeneous endpoints (Tyler cloud,
New World, PublicAccess). This base provides a resilient HTML/JSON fetch
that subclasses can override as recon deepens.
"""

from __future__ import annotations

import logging
import re
import time
from typing import List, Optional
from urllib.parse import urljoin

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,application/xhtml+xml,*/*;q=0.8",
}


class OdysseyBaseScraper(BaseScraper):
    """Subclasses provide ``county`` and ``base_url``."""

    @property
    def county(self) -> str:
        raise NotImplementedError

    @property
    def base_url(self) -> str:
        raise NotImplementedError

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        url = self.base_url
        logger.info(f"📥 Odyssey-style fetch for {self.county}: {url}")

        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 required")
            return []

        session = requests.Session()
        session.headers.update(HEADERS)
        records: List[ArrestRecord] = []

        try:
            resp = session.get(url, timeout=30, verify=False)
            if resp.status_code != 200:
                logger.warning(f"{self.county}: HTTP {resp.status_code} from {url}")
                return []

            # JSON path
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype or resp.text.strip().startswith(("{", "[")):
                try:
                    data = resp.json()
                    records = self._parse_json(data, url)
                    if records:
                        logger.info(
                            f"✅ {self.county}: {len(records)} JSON records in {time.time()-start:.1f}s"
                        )
                        return records
                except Exception:
                    pass

            soup = BeautifulSoup(resp.text, "html.parser")
            records = self._parse_html(soup, url)
            logger.info(
                f"✅ {self.county}: {len(records)} HTML records in {time.time()-start:.1f}s"
            )
            return records
        except Exception as e:
            logger.error(f"{self.county} Odyssey scrape failed: {e}")
            return []

    def _parse_json(self, data, source_url: str) -> List[ArrestRecord]:
        records: List[ArrestRecord] = []
        inmates = []
        if isinstance(data, list):
            inmates = data
        elif isinstance(data, dict):
            for key in ("data", "inmates", "bookings", "results", "items", "Data"):
                if key in data and isinstance(data[key], list):
                    inmates = data[key]
                    break
        for row in inmates:
            if not isinstance(row, dict):
                continue
            name = (
                row.get("name")
                or row.get("fullName")
                or row.get("FullName")
                or f"{row.get('lastName', row.get('LastName', ''))}, {row.get('firstName', row.get('FirstName', ''))}".strip(", ")
            )
            if not name or name.strip() in (",", ""):
                continue
            booking = str(
                row.get("bookingNumber")
                or row.get("BookingNumber")
                or row.get("id")
                or row.get("Id")
                or f"ODY_{int(time.time())}"
            )
            charges = row.get("charges") or row.get("Charges") or row.get("offense") or "Unknown"
            if isinstance(charges, list):
                charges = " | ".join(str(c) for c in charges)
            bond = str(row.get("bond") or row.get("BondAmount") or row.get("bondAmount") or "0")
            bond = re.sub(r"[^\d.]", "", bond) or "0"
            records.append(
                ArrestRecord(
                    County=self.county,
                    State=self.state or "FL",
                    Full_Name=str(name).strip(),
                    Booking_Number=booking,
                    Charges=str(charges),
                    Bond_Amount=bond,
                    Status="In Custody",
                    Detail_URL=source_url,
                )
            )
        return records

    def _parse_html(self, soup, source_url: str) -> List[ArrestRecord]:
        records: List[ArrestRecord] = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [c.get_text(" ", strip=True).lower() for c in rows[0].find_all(["th", "td"])]
            if not any(
                any(k in h for k in ("name", "inmate", "booking", "subject"))
                for h in headers
            ) and len(rows) < 3:
                continue
            for row in rows[1:]:
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue
                name = cells[0]
                if not name or len(name) < 2:
                    continue
                booking = cells[1] if len(cells) > 1 else f"ODY_{int(time.time()) % 100000}"
                charges = "Unknown"
                bond = "0"
                for i, h in enumerate(headers):
                    if i >= len(cells):
                        break
                    if "charge" in h or "offense" in h:
                        charges = cells[i]
                    elif "bond" in h or "bail" in h:
                        bond = re.sub(r"[^\d.]", "", cells[i]) or "0"
                    elif "book" in h and "date" not in h and "number" in h:
                        booking = cells[i] or booking
                records.append(
                    ArrestRecord(
                        County=self.county,
                        State=self.state or "FL",
                        Full_Name=name,
                        Booking_Number=str(booking),
                        Charges=charges,
                        Bond_Amount=bond,
                        Status="In Custody",
                        Detail_URL=source_url,
                    )
                )
            if records:
                break

        # Link-based inmate list fallback
        if not records:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True)
                if not text or len(text) < 3:
                    continue
                if any(x in href.lower() for x in ("inmate", "booking", "detail", "subject")):
                    bid = re.sub(r"\W+", "", href)[-20:] or f"ODY_{len(records)}"
                    records.append(
                        ArrestRecord(
                            County=self.county,
                            State=self.state or "FL",
                            Full_Name=text,
                            Booking_Number=bid,
                            Charges="Unknown",
                            Bond_Amount="0",
                            Status="In Custody",
                            Detail_URL=urljoin(source_url, href),
                        )
                    )
        return records
