"""
Shelby County (TN) Arrest Scraper — Memphis jail inmate lookup.

Official portals (IML — Inmate Lookup):
  - Jail (201 Poplar):  https://imljail.shelbycountytn.gov/IML
  - Penal Farm (SCDC):  https://imlscdc.shelbycountytn.gov/IML
  - Sheriff info page:  https://www.shelby-sheriff.org/jail-inmate-information

Search is name-based (first + last). Letter-prefix walk covers the roster.
Some TLS stacks fail handshake on the IML hosts — curl_cffi is tried first,
then requests, then fail closed.
"""
from __future__ import annotations

import hashlib
import logging
import re
import string
import time
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

IML_URLS = (
    "https://imljail.shelbycountytn.gov/IML",
    "https://imlscdc.shelbycountytn.gov/IML",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
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
        seen = set()

        for base in IML_URLS:
            try:
                batch = self._scrape_iml(base)
            except Exception as e:
                logger.warning(f"Shelby IML {base}: {e}")
                continue
            for rec in batch:
                if rec.Booking_Number in seen:
                    continue
                seen.add(rec.Booking_Number)
                records.append(rec)
            if records:
                break  # one working portal is enough

        logger.info(f"✅ Shelby (TN): {len(records)} records in {time.time() - start:.1f}s")
        return records

    def _scrape_iml(self, base_url: str) -> List[ArrestRecord]:
        session = self._make_session()
        if session is None:
            return []

        # GET landing to establish session + discover form fields
        html = self._get(session, base_url)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form")
        action = base_url
        if form and form.get("action"):
            action = form.get("action")
            if not action.startswith("http"):
                from urllib.parse import urljoin
                action = urljoin(base_url, action)

        out: List[ArrestRecord] = []
        seen = set()

        # Letter walk on last name; first name blank or single letter if required
        for letter in string.ascii_uppercase:
            try:
                payload = self._build_payload(soup, last_name=letter, first_name="")
                resp_html = self._post(session, action, payload)
                if not resp_html:
                    # Retry with first-name letter too
                    payload = self._build_payload(soup, last_name=letter, first_name=letter)
                    resp_html = self._post(session, action, payload)
                if not resp_html:
                    continue
                batch = self._parse_tables(resp_html, base_url)
                for rec in batch:
                    if rec.Booking_Number in seen:
                        continue
                    seen.add(rec.Booking_Number)
                    out.append(rec)
                # Refresh form tokens from latest response
                soup = BeautifulSoup(resp_html, "html.parser")
                time.sleep(0.2)
            except Exception as e:
                logger.debug(f"Shelby letter {letter}: {e}")

        return out

    def _make_session(self):
        """Prefer curl_cffi for TLS fingerprint; fall back to requests."""
        try:
            from curl_cffi import requests as crequests

            s = crequests.Session(impersonate="chrome131")
            s.headers.update(HEADERS)
            return ("curl", s)
        except Exception:
            pass
        try:
            import requests

            s = requests.Session()
            s.headers.update(HEADERS)
            s.verify = False
            return ("requests", s)
        except Exception as e:
            logger.error(f"Shelby: no HTTP client available: {e}")
            return None

    def _get(self, session_tuple, url: str) -> Optional[str]:
        kind, session = session_tuple
        try:
            if kind == "curl":
                resp = session.get(url, timeout=30)
            else:
                resp = session.get(url, timeout=30, verify=False)
            if resp.status_code != 200:
                return None
            return resp.text
        except Exception as e:
            logger.debug(f"Shelby GET {url}: {e}")
            return None

    def _post(self, session_tuple, url: str, data: dict) -> Optional[str]:
        kind, session = session_tuple
        try:
            if kind == "curl":
                resp = session.post(url, data=data, timeout=30)
            else:
                resp = session.post(url, data=data, timeout=30, verify=False)
            if resp.status_code != 200:
                return None
            return resp.text
        except Exception as e:
            logger.debug(f"Shelby POST: {e}")
            return None

    def _build_payload(self, soup: BeautifulSoup, last_name: str, first_name: str) -> dict:
        data = {}
        form = soup.find("form") if soup else None
        if form:
            for inp in form.find_all("input"):
                name = inp.get("name")
                if not name:
                    continue
                typ = (inp.get("type") or "text").lower()
                if typ in ("submit", "button", "image"):
                    continue
                data[name] = inp.get("value") or ""
            for sel in form.find_all("select"):
                name = sel.get("name")
                if not name:
                    continue
                opt = sel.find("option", selected=True) or sel.find("option")
                data[name] = opt.get("value") if opt else ""

        # Overlay name fields (common ASP.NET / custom names)
        assigned_last = assigned_first = False
        for key in list(data.keys()):
            kl = key.lower()
            if "last" in kl and "name" in kl:
                data[key] = last_name
                assigned_last = True
            elif kl in ("lastname", "last_name", "txtlastname", "lname"):
                data[key] = last_name
                assigned_last = True
            elif "first" in kl and "name" in kl:
                data[key] = first_name
                assigned_first = True
            elif kl in ("firstname", "first_name", "txtfirstname", "fname"):
                data[key] = first_name
                assigned_first = True

        if not assigned_last:
            data["LastName"] = last_name
            data["lastName"] = last_name
        if not assigned_first:
            data["FirstName"] = first_name
            data["firstName"] = first_name

        return data

    def _parse_tables(self, html: str, source_url: str) -> List[ArrestRecord]:
        soup = BeautifulSoup(html, "html.parser")
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
                    booking_num = (
                        f"SHE_{hashlib.md5(f'{name}|SHELBY_TN'.encode()).hexdigest()[:10]}"
                    )

                first, last = self._split_name(name)
                records.append(
                    ArrestRecord(
                        County=self.county,
                        State="TN",
                        Full_Name=name.title() if name.isupper() else name,
                        First_Name=first,
                        Last_Name=last,
                        Booking_Number=str(booking_num),
                        Booking_Date=booking_date,
                        Charges=charges or "Unknown",
                        Bond_Amount=bond,
                        Status="In Custody",
                        Detail_URL=source_url,
                        Facility="Shelby County Jail",
                        Agency="Shelby County Sheriff's Office",
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
