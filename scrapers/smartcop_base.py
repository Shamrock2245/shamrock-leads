"""
SmartCOP Base Scraper — Stealth Edition.
Handles SmartWebClient ASP.NET portals (e.g. Putnam, Sumter, Taylor, Bradford, Gilchrist).
Uses curl_cffi (Chrome TLS fingerprint) + APE proxy rotation for anti-detection.
"""
import logging
import re
import time
from datetime import datetime
from typing import List, Optional  # noqa: F401 — Optional used in proxy_attempts

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

IMPERSONATE = "chrome131"

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


class SmartCOPBaseScraper(BaseScraper):
    """
    Base scraper for SmartCOP/SmartWebClient ASP.NET jail portals.
    Subclasses must implement portal_url property.
    Uses curl_cffi for TLS fingerprint impersonation and APE for proxy rotation.
    """

    @property
    def portal_url(self) -> str:
        raise NotImplementedError("Subclass must define portal_url")

    def get_headers(self) -> dict:
        headers = dict(HEADERS)
        headers["Referer"] = self.portal_url
        return headers

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cffi_requests
            from bs4 import BeautifulSoup
        except ImportError:
            # Fallback to plain requests if curl_cffi not available
            return self._scrape_fallback()

        # SmartCOP hosts are often LAN-ish / port-custom and break through
        # HTTP CONNECT proxies (502). Prefer direct, then one residential hop.
        proxy_attempts: list[Optional[str]] = [None]
        if self.ape:
            p = self.get_proxy(prefer_residential=True)
            if p:
                proxy_attempts.append(p)

        headers = self.get_headers()
        records = []
        session = cffi_requests.Session()
        resp = None
        proxy_used: Optional[str] = None
        last_err: Optional[Exception] = None

        for proxy in proxy_attempts:
            proxies = {"http": proxy, "https": proxy} if proxy else None
            try:
                resp = session.get(
                    self.portal_url, headers=headers, timeout=30,
                    impersonate=IMPERSONATE, proxies=proxies, verify=False
                )
                if resp.status_code != 200:
                    raise Exception(f"HTTP {resp.status_code}")
                proxy_used = proxy
                break
            except Exception as e:
                last_err = e
                self.logger.warning(
                    f"{self.county}: portal GET failed (proxy={'yes' if proxy else 'direct'}): {e}"
                )
                if proxy:
                    self.record_proxy_failure(proxy)
                continue

        if resp is None:
            self.logger.warning(f"{self.county}: failed to load SmartCOP portal: {last_err}")
            return records

        soup = BeautifulSoup(resp.text, "html.parser")
        proxies = {"http": proxy_used, "https": proxy_used} if proxy_used else None

        def _hidden(name):
            el = soup.find("input", {"name": name})
            return el["value"] if el and el.get("value") else ""

        table = soup.find("table", id=lambda x: x and "GridView" in x)
        if not table:
            payload = {
                "__VIEWSTATE": _hidden("__VIEWSTATE"),
                "__VIEWSTATEGENERATOR": _hidden("__VIEWSTATEGENERATOR"),
                "__EVENTVALIDATION": _hidden("__EVENTVALIDATION"),
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
            }
            for btn in soup.find_all("input", {"type": "submit"}):
                name = btn.get("name", "")
                value = btn.get("value", "")
                if any(kw in value.lower() for kw in ["search", "view", "all", "find", "show"]):
                    payload[name] = value
                    break
            else:
                payload["ctl00$ContentPlaceHolder1$btnSearch"] = "Search"

            time.sleep(1.2)
            try:
                resp = session.post(
                    self.portal_url, data=payload, headers=headers,
                    timeout=60, impersonate=IMPERSONATE, proxies=proxies, verify=False
                )
                if resp.status_code != 200:
                    raise Exception(f"HTTP {resp.status_code}")
                soup = BeautifulSoup(resp.text, "html.parser")
                table = soup.find("table", id=lambda x: x and "GridView" in x)
            except Exception as e:
                self.logger.error(f"{self.county}: POST search failed: {e}")
                if proxy_used:
                    self.record_proxy_failure(proxy_used)
                return records

        if not table:
            for t in soup.find_all("table"):
                text = t.get_text(" ").lower()
                if any(kw in text for kw in ["name", "booking", "inmate"]):
                    rows = t.find_all("tr")
                    if len(rows) > 1:
                        table = t
                        break

        if not table:
            self.logger.warning(f"{self.county}: no data table found")
            return records

        rows = table.find_all("tr")
        for row in rows[1:]:
            cols = row.find_all("td")
            if len(cols) < 4:
                continue
            texts = [c.text.strip() for c in cols]

            name_col = texts[1] if len(texts) > 1 else texts[0]
            if "," in name_col:
                parts = name_col.split(",", 1)
                last_name = parts[0].strip()
                first_name = parts[1].strip()
            else:
                last_name = name_col
                first_name = ""

            booking_date_str = texts[2] if len(texts) > 2 else ""
            try:
                dt = datetime.strptime(booking_date_str.split(" ")[0], "%m/%d/%Y")
                booking_date = dt.strftime("%Y-%m-%d")
            except (ValueError, IndexError):
                booking_date = datetime.now().strftime("%Y-%m-%d")

            booking_number = f"{last_name.upper()}_{booking_date.replace('-', '')}"
            charges = " | ".join([t for t in texts[3:] if t and len(t) > 3])
            full_name = f"{last_name}, {first_name}".strip().rstrip(",").strip()

            records.append(ArrestRecord(
                County=self.county,
                State=(getattr(self, "state", None) or "FL"),
                Booking_Number=booking_number,
                Full_Name=full_name,
                First_Name=first_name,
                Last_Name=last_name,
                Booking_Date=booking_date,
                Charges=charges,
                Bond_Amount="0",
                Status="In Custody",
                Detail_URL=self.portal_url,
            ))

        if proxy_used and records:
            self.record_proxy_success(proxy_used)
        self.logger.info(f"{self.county}: {len(records)} records (SmartCOP stealth)")
        return records

    def _scrape_fallback(self) -> List[ArrestRecord]:
        """Fallback using plain requests if curl_cffi unavailable."""
        import requests
        from bs4 import BeautifulSoup

        records = []
        try:
            session = requests.Session()
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            resp = session.get(self.portal_url, headers=headers, timeout=30, verify=False)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            viewstate = soup.find("input", {"name": "__VIEWSTATE"})
            if not viewstate:
                return records

            table = soup.find("table", id=lambda x: x and "GridView" in x)
            if not table:
                payload = {
                    "__VIEWSTATE": viewstate["value"] if viewstate else "",
                    "__VIEWSTATEGENERATOR": (soup.find("input", {"name": "__VIEWSTATEGENERATOR"}) or {}).get("value", ""),
                    "__EVENTVALIDATION": (soup.find("input", {"name": "__EVENTVALIDATION"}) or {}).get("value", ""),
                    "ctl00$ContentPlaceHolder1$btnSearch": "Search",
                }
                resp = session.post(self.portal_url, data=payload, headers=headers, timeout=30, verify=False)
                soup = BeautifulSoup(resp.text, "html.parser")
                table = soup.find("table", id=lambda x: x and "GridView" in x)

            if not table:
                return records

            rows = table.find_all("tr")
            for row in rows[1:]:
                cols = row.find_all("td")
                if len(cols) < 4:
                    continue
                texts = [c.text.strip() for c in cols]
                name_col = texts[1]
                if "," in name_col:
                    parts = name_col.split(",", 1)
                    last_name = parts[0].strip()
                    first_name = parts[1].strip()
                else:
                    last_name = name_col
                    first_name = ""

                booking_date_str = texts[2]
                try:
                    dt = datetime.strptime(booking_date_str.split(" ")[0], "%m/%d/%Y")
                    booking_date = dt.strftime("%Y-%m-%d")
                except (ValueError, IndexError):
                    booking_date = datetime.now().strftime("%Y-%m-%d")

                booking_number = f"{last_name.upper()}_{booking_date.replace('-', '')}"
                charges = " | ".join([t for t in texts[3:] if t and len(t) > 3])
                full_name = f"{last_name}, {first_name}".strip().rstrip(",").strip()

                records.append(ArrestRecord(
                    County=self.county,
                    State=(getattr(self, "state", None) or "FL"),
                    Booking_Number=booking_number,
                    Full_Name=full_name,
                    First_Name=first_name,
                    Last_Name=last_name,
                    Booking_Date=booking_date,
                    Charges=charges,
                    Bond_Amount="0",
                    Status="In Custody",
                    Detail_URL=self.portal_url,
                ))
            self.logger.info(f"{self.county}: {len(records)} records (SmartCOP fallback)")
        except Exception as e:
            self.logger.error(f"{self.county}: SmartCOP fallback error: {e}")
        return records
