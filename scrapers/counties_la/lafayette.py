"""
Lafayette Parish (LA) Arrest Scraper — 365Labs Community Portal.

Portal: https://community.365labs.com/{agency}/inmatelist
Agency: LPSO (Lafayette Parish Sheriff's Office)
Docs:   https://lafayettesheriff.com/services/corrections/offender-information/

365Labs requires captcha verification (GetCaptcha / VerifyCaptcha) before
returning the inmate list. This scraper:
  1) Tries public API-ish paths without captcha
  2) Falls back to DrissionPage for JS-rendered portal
  3) Fails closed (empty list) if captcha blocks — never invents records
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import List, Tuple

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

AGENCY_ID = "689d71d5-1d4e-4726-9cfb-a3c94dfb231e"
PORTAL_URL = f"https://community.365labs.com/{AGENCY_ID}/inmatelist"
API_BASE = "https://365core.azurewebsites.us/api"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Referer": PORTAL_URL,
}


class LafayetteScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Lafayette"

    @property
    def state(self) -> str:
        return "LA"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []

        # Strategy 1: probe JSON endpoints (may 401/captcha)
        records = self._try_api()
        if records:
            logger.info(
                f"✅ Lafayette (LA): {len(records)} via API in {time.time() - start:.1f}s"
            )
            return records

        # Strategy 2: browser render
        records = self._scrape_with_browser()
        logger.info(
            f"✅ Lafayette (LA): {len(records)} records in {time.time() - start:.1f}s"
        )
        return records

    def _try_api(self) -> List[ArrestRecord]:
        session = requests.Session()
        session.headers.update(HEADERS)
        session.verify = False
        candidates = [
            f"{API_BASE}/agencies/{AGENCY_ID}/inmates",
            f"{API_BASE}/agencies/{AGENCY_ID}/inmatelist",
            f"{API_BASE}/public/agencies/{AGENCY_ID}/inmates",
            f"https://community.365labs.com/api/agencies/{AGENCY_ID}/inmates",
        ]
        for url in candidates:
            try:
                resp = session.get(url, timeout=20)
                if resp.status_code != 200:
                    continue
                ctype = resp.headers.get("Content-Type", "")
                if "json" not in ctype and not resp.text.strip().startswith(("[", "{")):
                    continue
                data = resp.json()
                records = self._parse_json(data)
                if records:
                    return records
            except Exception as e:
                logger.debug(f"Lafayette API {url}: {e}")
        return []

    def _scrape_with_browser(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage

            co = self._get_browser_options()
            page = ChromiumPage(co)
            page.get(PORTAL_URL)
            page.wait.doc_loaded()
            time.sleep(4)

            html = page.html
            soup = BeautifulSoup(html, "html.parser")
            records = self._parse_html(soup)

            # Try intercepting XHR if table empty
            if not records:
                try:
                    # Scroll / wait for SPA load
                    time.sleep(3)
                    html = page.html
                    soup = BeautifulSoup(html, "html.parser")
                    records = self._parse_html(soup)
                except Exception:
                    pass

            try:
                page.quit()
            except Exception:
                pass
            return records
        except Exception as e:
            logger.debug(f"Lafayette browser fallback: {e}")
            return []

    def _parse_json(self, data) -> List[ArrestRecord]:
        inmates = []
        if isinstance(data, list):
            inmates = data
        elif isinstance(data, dict):
            for key in ("data", "inmates", "items", "results", "value", "records"):
                if isinstance(data.get(key), list):
                    inmates = data[key]
                    break
        out: List[ArrestRecord] = []
        for row in inmates:
            if not isinstance(row, dict):
                continue
            name = (
                row.get("name")
                or row.get("fullName")
                or row.get("full_name")
                or f"{row.get('lastName', row.get('last_name', ''))}, "
                   f"{row.get('firstName', row.get('first_name', ''))}".strip(", ")
            )
            if not name or name.strip() in (",", ""):
                continue
            booking = str(
                row.get("bookingNumber")
                or row.get("booking_number")
                or row.get("id")
                or f"LAF_{hashlib.md5(f'{name}|LAF_LA'.encode()).hexdigest()[:10]}"
            )
            charges = row.get("charges") or row.get("offense") or "Unknown"
            if isinstance(charges, list):
                charges = " | ".join(
                    str(c.get("description", c) if isinstance(c, dict) else c)
                    for c in charges
                )
            bond = str(row.get("bond") or row.get("bondAmount") or "0")
            bond = re.sub(r"[^\d.]", "", bond) or "0"
            first = str(row.get("firstName", row.get("first_name", ""))).strip()
            last = str(row.get("lastName", row.get("last_name", ""))).strip()
            if not first and "," in name:
                first, last = self._split_name(name)
            out.append(
                ArrestRecord(
                    County=self.county,
                    State="LA",
                    Full_Name=str(name).strip(),
                    First_Name=first,
                    Last_Name=last,
                    Booking_Number=booking,
                    Charges=str(charges),
                    Bond_Amount=bond,
                    Status="In Custody",
                    Facility="Lafayette Parish Correctional Center",
                    Agency="Lafayette Parish Sheriff's Office",
                    Detail_URL=PORTAL_URL,
                )
            )
        return out

    def _parse_html(self, soup: BeautifulSoup) -> List[ArrestRecord]:
        records: List[ArrestRecord] = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [
                th.get_text(" ", strip=True).lower()
                for th in rows[0].find_all(["th", "td"])
            ]
            if not any(k in " ".join(headers) for k in ("name", "inmate", "booking")):
                continue
            for row in rows[1:]:
                cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue
                name = cells[0]
                if not name or len(name) < 2:
                    continue
                booking = ""
                charges = "Unknown"
                bond = "0"
                for i, h in enumerate(headers):
                    if i >= len(cells):
                        break
                    if "book" in h and "date" not in h:
                        booking = cells[i]
                    elif "charge" in h or "offense" in h:
                        charges = cells[i]
                    elif "bond" in h or "bail" in h:
                        bond = re.sub(r"[^\d.]", "", cells[i]) or "0"
                if not booking:
                    booking = (
                        f"LAF_{hashlib.md5(f'{name}|LAF_LA'.encode()).hexdigest()[:10]}"
                    )
                first, last = self._split_name(name)
                records.append(
                    ArrestRecord(
                        County=self.county,
                        State="LA",
                        Full_Name=name.title() if name.isupper() else name,
                        First_Name=first,
                        Last_Name=last,
                        Booking_Number=str(booking),
                        Charges=charges or "Unknown",
                        Bond_Amount=bond,
                        Status="In Custody",
                        Facility="Lafayette Parish Correctional Center",
                        Agency="Lafayette Parish Sheriff's Office",
                        Detail_URL=PORTAL_URL,
                    )
                )
            if records:
                break
        return records

    @staticmethod
    def _split_name(name: str) -> Tuple[str, str]:
        if "," in name:
            parts = name.split(",", 1)
            return parts[1].strip().title(), parts[0].strip().title()
        bits = name.split()
        if len(bits) >= 2:
            return bits[0].title(), bits[-1].title()
        return name.title(), ""
