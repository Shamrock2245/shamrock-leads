"""
Gadsden County Arrest Scraper — Custom HTML (Inmate Lookup)
Source: Gadsden County Sheriff's Office
URL: https://gadsdensheriff.com/inmate-lookup/
Method: requests GET — HTML page (currently appears blank; may load via JS)
Status: NEEDS RECON — page appears blank; may require JS rendering

NOTE: The official Gadsden County Sheriff inmate lookup page exists but currently
renders blank content. This scraper attempts a GET and parses any available HTML.
If the page loads via JavaScript, a DrissionPage browser fallback is used.
"""

import logging
import re
from typing import List

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

from curl_cffi import requests as cffi_requests
logger = logging.getLogger(__name__)

ROSTER_URL = "https://gadsdensheriff.com/inmate-lookup/"
FACILITY = "Gadsden County Jail"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://gadsdensheriff.com/",
}

class GadsdenCountyScraper(BaseScraper):
    """Gadsden County (FL) — Inmate lookup (Quincy area). Needs recon."""

    @property
    def county(self) -> str:
        return "Gadsden"

    def scrape(self) -> List[ArrestRecord]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("requests/bs4 not installed")
            raise

        # Attempt HTTP GET
        try:
            resp = cffi_requests.get(ROSTER_URL, headers=HEADERS, timeout=30, impersonate=IMPERSONATE)
            if resp.status_code == 200 and len(resp.text) > 500:
                soup = BeautifulSoup(resp.text, "html.parser")
                records = self._parse_html(soup)
                if records:
                    logger.info(f"Gadsden HTML: {len(records)} records")
                    return records
        except requests.RequestException as e:
            logger.debug(f"Gadsden HTTP failed: {e}")

        # Browser fallback for JS-rendered content
        try:
            from DrissionPage import ChromiumPage
            co = self._get_browser_options()
            page = ChromiumPage(addr_or_opts=co)
            try:
                page.get(ROSTER_URL)
                import time
                time.sleep(4)
                html = page.html
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")
                records = self._parse_html(soup)
                if records:
                    logger.info(f"Gadsden browser: {len(records)} records")
                    return records
                else:
                    logger.warning("Gadsden: page loaded but no records found — roster may be blank or require login")
            finally:
                try:
                    page.quit()
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Gadsden browser fallback failed: {e}")
            raise

        raise RuntimeError("Gadsden: 0 records — needs recon (roster may be blank or JS-gated)")

    def _parse_html(self, soup) -> List[ArrestRecord]:
        records = []
        seen = set()

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_text = rows[0].get_text(" ").lower()
            if not any(k in header_text for k in ["name", "inmate", "booking", "detainee"]):
                continue
            headers = [th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])]
            col = {h: i for i, h in enumerate(headers)}

            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if not cells:
                    continue
                name_idx = col.get("name", col.get("inmate name", 0))
                full_name = cells[name_idx] if name_idx < len(cells) else cells[0]
                if not full_name or len(full_name) < 3:
                    continue
                bd_idx = None
                for k in ["booking date", "booking", "date"]:
                    if k in col:
                        bd_idx = col[k]
                        break
                booking_date = cells[bd_idx] if bd_idx is not None and bd_idx < len(cells) else ""
                bn_idx = None
                for k in ["booking #", "booking no", "booking number", "id"]:
                    if k in col:
                        bn_idx = col[k]
                        break
                booking_num = cells[bn_idx] if bn_idx is not None and bn_idx < len(cells) else ""
                key = booking_num or full_name
                if key in seen:
                    continue
                seen.add(key)
                f, m, l = self._parse_name(full_name)
                records.append(ArrestRecord(
                    County=self.county,
                    Booking_Number=booking_num,
                    Full_Name=full_name,
                    First_Name=f, Middle_Name=m, Last_Name=l,
                        DOB="",
                    Booking_Date=booking_date,
                    Status="In Custody",
                        Release_Date="",
                    Facility=FACILITY,
                    Detail_URL=ROSTER_URL,

                    LastCheckedMode="INITIAL",
                ))
            if records:
                break
        return records

    @staticmethod
    def _parse_name(name: str):
        if not name:
            return "", "", ""
        name = " ".join(name.strip().split())
        if "," in name:
            parts = name.split(",", 1)
            last = parts[0].strip()
            fm = parts[1].strip().split()
            first = fm[0] if fm else ""
            middle = " ".join(fm[1:]) if len(fm) > 1 else ""
            return first, middle, last
        parts = name.split()
        if len(parts) == 1:
            return parts[0], "", ""
        if len(parts) == 2:
            return parts[0], "", parts[1]
        return parts[0], " ".join(parts[1:-1]), parts[-1]
