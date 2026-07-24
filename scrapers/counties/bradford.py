"""
Bradford County (FL) Arrest Scraper — SmartCOP ASP.NET.
Source: Bradford County Sheriff's Office
URL: http://smartweb.bradfordsheriff.org/smartwebclient/Jail.aspx
Method: curl_cffi (chrome131 impersonation) + BeautifulSoup — ASP.NET ViewState form
Stealth: Direct-first (proxy CONNECT 502s common), then APE residential hop
"""
import logging
import re
import time
from typing import List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

# smartcop.bradfordsheriff.org no longer resolves — live host is smartweb.* (HTTP only)
BASE_URL = "http://smartweb.bradfordsheriff.org"
SEARCH_URL = f"{BASE_URL}/smartwebclient/Jail.aspx"
FACILITY = "Bradford County Jail"
IMPERSONATE = "chrome131"

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": SEARCH_URL,
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}


class BradfordCountyScraper(BaseScraper):
    """Bradford County (FL) — SmartCOP ASP.NET jail roster."""

    @property
    def county(self) -> str:
        return "Bradford"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cffi_requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("curl_cffi/bs4 not installed")
            raise

        # Direct first — APE CONNECT tunnels often 502 these SmartWEB hosts
        proxy_attempts: list[Optional[str]] = [None]
        if self.ape:
            p = self.get_proxy(prefer_residential=True)
            if p:
                proxy_attempts.append(p)

        session = cffi_requests.Session()
        resp = None
        proxy_used: Optional[str] = None
        last_err: Optional[Exception] = None
        for proxy in proxy_attempts:
            proxies = {"http": proxy, "https": proxy} if proxy else None
            try:
                resp = session.get(
                    SEARCH_URL, headers=HEADERS, timeout=30,
                    impersonate=IMPERSONATE, proxies=proxies, verify=False,
                )
                if resp.status_code != 200:
                    raise Exception(f"{resp.status_code} loading page")
                proxy_used = proxy
                break
            except Exception as e:
                last_err = e
                logger.warning(f"Bradford GET failed (proxy={'yes' if proxy else 'direct'}): {e}")
                if proxy:
                    self.record_proxy_failure(proxy)
        if resp is None:
            raise Exception(f"Bradford: failed to load page: {last_err}")

        soup = BeautifulSoup(resp.text, "html.parser")
        proxies = {"http": proxy_used, "https": proxy_used} if proxy_used else None

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

        # SmartWEB uses a misspelled submit control "btnSumit"
        for btn in soup.find_all("input", {"type": "submit"}):
            name = btn.get("name", "")
            value = btn.get("value", "")
            if name == "btnSumit" or any(kw in value.lower() for kw in ["search", "view", "all", "find", "show", "submit"]):
                post_data[name] = value
                break
        else:
            post_data["btnSumit"] = "Submit"

        time.sleep(1.5)

        try:
            resp2 = session.post(
                SEARCH_URL, data=post_data, headers=HEADERS,
                timeout=60, impersonate=IMPERSONATE, proxies=proxies, verify=False,
            )
            if resp2.status_code != 200:
                raise Exception(f"{resp2.status_code} on POST")
        except Exception as e:
            logger.error(f"Bradford: POST failed ({e})")
            if proxy_used:
                self.record_proxy_failure(proxy_used)
            raise

        from scrapers.smartweb_card_parser import parse_smartweb_cards
        records = parse_smartweb_cards(
            resp2.text,
            county=self.county,
            state="FL",
            facility=FACILITY,
            detail_url=SEARCH_URL,
        )
        # Fallback if card parse yields nothing (layout regression)
        if not records:
            from bs4 import BeautifulSoup as _BS
            records = self._parse_table(_BS(resp2.text, "html.parser"))
        if proxy_used and records:
            self.record_proxy_success(proxy_used)
        logger.info(f"Bradford: {len(records)} records")
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

            f, m, l = self._parse_name(full_name)
            bond_amount = self._parse_bond(bond_raw)

            records.append(ArrestRecord(
                County=self.county,
                State="FL",
                Booking_Number=self._clean(booking_num) or f"{l.upper()}_{booking_date.replace('-', '').replace('/', '')}",
                Full_Name=full_name,
                First_Name=f,
                Middle_Name=m,
                Last_Name=l,
                Booking_Date=self._clean(booking_date),
                Status="In Custody",
                Facility=FACILITY,
                Charges=self._clean(charges),
                Bond_Amount=str(bond_amount) if bond_amount > 0 else "0",
                Detail_URL=detail_url,
            ))
        return records

    @staticmethod
    def _clean(text):
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    @staticmethod
    def _parse_name(n):
        if not n:
            return "", "", ""
        n = " ".join(n.strip().split())
        if "," in n:
            p = n.split(",", 1)
            l = p[0].strip()
            fm = p[1].strip().split()
            return (fm[0] if fm else ""), (" ".join(fm[1:]) if len(fm) > 1 else ""), l
        p = n.split()
        return p[0], (" ".join(p[2:]) if len(p) > 2 else ""), (p[-1] if len(p) >= 2 else "")

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
