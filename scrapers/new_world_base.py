"""
Base scraper for New World InmateInquiry portals.

Used by Flagler/Nassau/Walton (FL), Paulding/Henry (GA), Lancaster (SC), etc.
Pattern: server-rendered HTML listing + optional /Inmate/Detail/{id} pages.
"""

from __future__ import annotations

import logging
import re
import time
from typing import List, Optional, Tuple
from urllib.parse import urljoin

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_PAGES = 10
MAX_DETAILS = 250


class NewWorldBaseScraper(BaseScraper):
    """Subclasses set ``county`` and ``base_url`` (or ``portal_url``)."""

    MAX_PAGES = MAX_PAGES
    MAX_DETAILS = MAX_DETAILS

    @property
    def county(self) -> str:
        raise NotImplementedError

    @property
    def base_url(self) -> str:
        portal = getattr(type(self), "portal_url", None)
        if isinstance(portal, property):
            return self.portal_url  # type: ignore[attr-defined]
        if hasattr(self, "portal_url"):
            try:
                return self.portal_url  # type: ignore[attr-defined]
            except Exception:
                pass
        raise NotImplementedError("Subclasses must define base_url or portal_url")

    def scrape(self) -> List[ArrestRecord]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("bs4 not installed")
            return []

        try:
            from curl_cffi import requests as http
            use_impersonate = True
        except ImportError:
            import requests as http
            use_impersonate = False

        start = time.time()
        session = http.Session()
        base = self.base_url.rstrip("/")
        records: List[ArrestRecord] = []

        inmate_links: List[Tuple[str, str]] = []
        params = {"InCustody": "True"}

        for page_num in range(1, self.MAX_PAGES + 1):
            try:
                page_params = dict(params)
                if page_num > 1:
                    page_params["Page"] = str(page_num)

                kwargs = {"headers": HEADERS, "timeout": 30, "params": page_params}
                if use_impersonate:
                    kwargs["impersonate"] = "chrome131"
                resp = session.get(base, **kwargs)
                if resp.status_code != 200:
                    logger.warning(f"{self.county} NewWorld page {page_num}: HTTP {resp.status_code}")
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                page_links: List[Tuple[str, str]] = []
                for a_tag in soup.find_all("a", href=True):
                    href = a_tag["href"]
                    if "/Inmate/Detail/" in href or "/InmateInquiry/" in href and "Detail" in href:
                        name = a_tag.get_text(strip=True)
                        if name and name not in ("Back to Search", "Search"):
                            full_url = href if href.startswith("http") else urljoin(base + "/", href)
                            page_links.append((name, full_url))

                if not page_links:
                    # Fallback: parse table rows on listing page
                    page_records = self._parse_listing_table(soup, base)
                    if page_records:
                        records.extend(page_records)
                        if len(page_records) < 5:
                            break
                        continue
                    break

                inmate_links.extend(page_links)
                if len(page_links) < 5:
                    break
            except Exception as e:
                logger.error(f"{self.county} NewWorld page {page_num} failed: {e}")
                break

        # Dedup links
        seen = set()
        unique_links = []
        for name, url in inmate_links:
            if url not in seen:
                seen.add(url)
                unique_links.append((name, url))

        for name, detail_url in unique_links[: self.MAX_DETAILS]:
            try:
                kwargs = {"headers": HEADERS, "timeout": 25}
                if use_impersonate:
                    kwargs["impersonate"] = "chrome131"
                resp = session.get(detail_url, **kwargs)
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")
                rec = self._parse_detail(soup, name, detail_url)
                if rec:
                    records.append(rec)
                time.sleep(0.3)
            except Exception as e:
                logger.debug(f"{self.county} detail parse error: {e}")

        # If no detail links, try one more listing-only parse of page 1
        if not records:
            try:
                kwargs = {"headers": HEADERS, "timeout": 30, "params": params}
                if use_impersonate:
                    kwargs["impersonate"] = "chrome131"
                resp = session.get(base, **kwargs)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    records = self._parse_listing_table(soup, base)
            except Exception as e:
                logger.error(f"{self.county} NewWorld listing fallback failed: {e}")

        logger.info(
            f"✅ {self.county}: NewWorld scraped {len(records)} records in {time.time() - start:.1f}s"
        )
        return records

    def _parse_listing_table(self, soup, base_url: str) -> List[ArrestRecord]:
        records: List[ArrestRecord] = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])]
            if not any(h in headers for h in ("name", "inmate", "subject", "booking")):
                # still try if many data rows
                if len(rows) < 3:
                    continue
            for row in rows[1:]:
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue
                name = cells[0]
                if not name or len(name) < 2:
                    continue
                booking = ""
                bond = "0"
                charges = "Unknown"
                for i, h in enumerate(headers):
                    if i >= len(cells):
                        break
                    if "book" in h and "date" not in h:
                        booking = cells[i]
                    elif "bond" in h:
                        bond = re.sub(r"[^\d.]", "", cells[i]) or "0"
                    elif "charge" in h or "offense" in h:
                        charges = cells[i]
                if not booking:
                    booking = f"NW_{re.sub(r'[^A-Za-z0-9]', '', name)[:20]}_{int(time.time()) % 100000}"
                records.append(
                    ArrestRecord(
                        County=self.county,
                        State=self.state or "FL",
                        Full_Name=name,
                        Booking_Number=booking,
                        Charges=charges,
                        Bond_Amount=bond,
                        Status="In Custody",
                        Detail_URL=base_url,
                    )
                )
            if records:
                break
        return records

    def _parse_detail(self, soup, fallback_name: str, detail_url: str) -> Optional[ArrestRecord]:
        text_map = {}
        # Label/value pairs common on New World detail pages
        for row in soup.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                label = cells[0].get_text(" ", strip=True).lower().rstrip(":")
                value = cells[1].get_text(" ", strip=True)
                if label and value:
                    text_map[label] = value

        # Also dt/dd
        for dt in soup.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                text_map[dt.get_text(" ", strip=True).lower().rstrip(":")] = dd.get_text(" ", strip=True)

        def pick(*keys: str) -> str:
            for k in keys:
                for mk, mv in text_map.items():
                    if k in mk:
                        return mv
            return ""

        name = pick("name", "inmate") or fallback_name
        booking = pick("booking number", "booking #", "book number", "subject number", "jacket")
        if not booking:
            m = re.search(r"/Detail/([^/?#]+)", detail_url)
            booking = m.group(1) if m else f"NW_{int(time.time())}"

        dob = pick("date of birth", "dob", "birth")
        booking_date = pick("booking date", "book date", "arrest date")
        charges = pick("charge", "offense", "charges") or "Unknown"
        bond_raw = pick("bond", "bail", "total bond")
        bond = re.sub(r"[^\d.]", "", bond_raw) or "0"
        status = pick("status", "custody") or "In Custody"
        if status and "releas" in status.lower():
            custody = "Released"
        else:
            custody = "In Custody"

        first = last = middle = ""
        if "," in name:
            parts = [p.strip() for p in name.split(",", 1)]
            last = parts[0]
            rest = parts[1].split() if len(parts) > 1 else []
            first = rest[0] if rest else ""
            middle = " ".join(rest[1:]) if len(rest) > 1 else ""
        else:
            parts = name.split()
            first = parts[0] if parts else ""
            last = parts[-1] if len(parts) > 1 else ""
            middle = " ".join(parts[1:-1]) if len(parts) > 2 else ""

        return ArrestRecord(
            County=self.county,
            State=self.state or "FL",
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=str(booking),
            Booking_Date=booking_date,
            DOB=dob,
            Charges=charges,
            Bond_Amount=bond,
            Status=custody,
            Detail_URL=detail_url,
        )
