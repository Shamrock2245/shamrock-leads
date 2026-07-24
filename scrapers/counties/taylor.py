"""
Taylor County Arrest Scraper — SmartCOP ASP.NET.
Source: Taylor County Sheriff's Office
Public entry: http://jail.taylorsheriff.org/ → redirects to
  http://smartcop.taylorsheriff.org:8989/SmartWEBClient/Jail.aspx
(HTTPS :443 is dead; use HTTP on port 8989.)
"""
import logging
import re
import time
from typing import List
from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# Canonical portal (custom port). Entry host jail.taylorsheriff.org redirects here.
BASE_URL = "http://smartcop.taylorsheriff.org:8989"
SEARCH_URL = f"{BASE_URL}/SmartWEBClient/Jail.aspx"
FACILITY = "Taylor County Jail"
IMPERSONATE = "chrome131"
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "http://jail.taylorsheriff.org/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-User": "?1",
}


class TaylorCountyScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Taylor"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cffi_requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("curl_cffi/bs4 not installed"); raise

        session = cffi_requests.Session()
        # Direct only — CONNECT proxies rarely support custom ports like :8989
        try:
            resp = session.get(
                SEARCH_URL, headers=HEADERS, timeout=30,
                impersonate=IMPERSONATE, verify=False,
            )
            if resp.status_code != 200:
                # Fallback via public entry host (302 → :8989)
                resp = session.get(
                    "http://jail.taylorsheriff.org/",
                    headers=HEADERS, timeout=30,
                    impersonate=IMPERSONATE, verify=False, allow_redirects=True,
                )
            if resp.status_code != 200:
                raise Exception(f"{resp.status_code} error")
        except Exception as e:
            logger.error(f"Taylor: failed to load page: {e}"); raise

        soup = BeautifulSoup(resp.text, "html.parser")

        def _get_hidden(name):
            el = soup.find("input", {"name": name})
            return el["value"] if el and el.get("value") else ""

        post_data = {
            "__VIEWSTATE": _get_hidden("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _get_hidden("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": _get_hidden("__EVENTVALIDATION"),
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
        }

        for btn in soup.find_all("input", {"type": "submit"}):
            name = btn.get("name", "")
            value = btn.get("value", "")
            if any(kw in value.lower() for kw in ["search", "view", "all", "find", "show"]):
                post_data[name] = value
                break

        post_url = getattr(resp, "url", None) or SEARCH_URL
        # Prefer SmartWEB's btnSumit
        if "btnSumit" not in post_data:
            for btn in soup.find_all("input", {"type": "submit"}):
                if btn.get("name") == "btnSumit":
                    post_data["btnSumit"] = btn.get("value") or "Submit"
                    break
            else:
                post_data["btnSumit"] = "Submit"
        try:
            resp2 = session.post(
                post_url, data=post_data, headers={**HEADERS, "Referer": post_url},
                timeout=60, impersonate=IMPERSONATE, verify=False,
            )
            if resp2.status_code != 200:
                raise Exception(f"{resp2.status_code} error")
        except Exception as e:
            logger.error(f"Taylor: POST failed ({e})")
            raise

        from scrapers.smartweb_card_parser import parse_smartweb_cards
        records = parse_smartweb_cards(
            resp2.text,
            county=self.county,
            state="FL",
            facility=FACILITY,
            detail_url=SEARCH_URL,
        )
        if not records:
            records = self._parse_table(BeautifulSoup(resp2.text, "html.parser"))
        logger.info(f"Taylor: {len(records)} records")
        return records

    def _parse_table(self, soup) -> List[ArrestRecord]:
        records = []
        table = None
        for t in soup.find_all("table"):
            text = t.get_text(" ").lower()
            if any(kw in text for kw in ["name", "booking", "inmate", "arrest"]):
                rows = t.find_all("tr")
                if len(rows) > 1:
                    table = t
                    break

        if not table:
            return []

        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            if not any(texts):
                continue

            full_name = texts[0] if len(texts) > 0 else ""
            booking_num = texts[1] if len(texts) > 1 else ""
            booking_date = texts[2] if len(texts) > 2 else ""
            charges = texts[3] if len(texts) > 3 else ""
            bond_raw = texts[4] if len(texts) > 4 else "0"

            if not full_name:
                continue

            detail_url = ""
            link = row.find("a", href=True)
            if link:
                href = link["href"]
                if not href.startswith("http"):
                    href = f"{BASE_URL}/{href.lstrip('/')}"
                detail_url = href

            f, m, l = self._pn(full_name)
            bond_amount = self._parse_bond(bond_raw)

            records.append(ArrestRecord(
                County=self.county,
                Booking_Number=self._clean(booking_num),
                Full_Name=full_name,
                First_Name=f,
                Middle_Name=m,
                Last_Name=l,
                        DOB="",
                Booking_Date=self._clean(booking_date),
                Status="In Custody",
                        Release_Date="",
                Facility=FACILITY,
                Charges=self._clean(charges),
                Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                Detail_URL=detail_url,
                LastCheckedMode="INITIAL",
            ))

        return records

    @staticmethod
    def _clean(text):
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    @staticmethod
    def _pn(n):
        if not n:
            return "", "", ""
        n = " ".join(n.strip().split())
        if "," in n:
            p = n.split(",", 1)
            l = p[0].strip()
            fm = p[1].strip().split()
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm) > 1 else ""), l
        p = n.split()
        return p[0], (" ".join(p[2:]) if len(p) > 2 else ""), p[-1] if len(p) >= 2 else ""

    @staticmethod
    def _parse_bond(bond_str):
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", bond_str.strip().upper())
        if any(t in cleaned for t in ["NOBOND", "NONE", "N/A", "HOLD"]):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
