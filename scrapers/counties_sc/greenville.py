"""
Greenville County (SC) Arrest Scraper.

Official portal:
  https://app.greenvillecounty.org/inmate_search.htm
  (disclaimer gate: greenvillecounty.org/disclaimer/InmateSearch.aspx)

Blocked from datacenter IPs by Imperva/Incapsula (HTTP 403 challenge iframe).
This scraper attempts, in order:
  1. Direct HTTP (works if IP is allowlisted)
  2. curl_cffi Chrome TLS impersonation
  3. DrissionPage headless browser
  4. Optional SOCKS5 residential proxy via SOCKS_PROXY / GREENVILLE_SOCKS_PROXY

When all paths fail, returns [] and logs loudly (fail closed).
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import List, Optional
from urllib.parse import urljoin

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PORTAL_URL = "https://app.greenvillecounty.org/inmate_search.htm"
DISCLAIMER_URL = "https://www.greenvillecounty.org/disclaimer/InmateSearch.aspx"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class GreenvilleSCScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Greenville"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        html = self._fetch_portal_html()
        if not html:
            logger.error(
                "Greenville: portal unreachable (Incapsula). "
                "Needs residential SOCKS (GREENVILLE_SOCKS_PROXY / SOCKS_PROXY) "
                "or office tunnel. Returning empty."
            )
            return []

        records = self._parse_results_html(html)
        # If landing page is a search form without results, try letter search
        if not records and self._looks_like_search_form(html):
            records = self._letter_search()

        logger.info(
            f"Greenville: {len(records)} records in {time.time() - start:.1f}s"
        )
        return records

    def _socks(self) -> Optional[str]:
        return (
            os.getenv("GREENVILLE_SOCKS_PROXY")
            or os.getenv("SOCKS_PROXY")
            or os.getenv("RESIDENTIAL_SOCKS")
            or ""
        ) or None

    def _fetch_portal_html(self) -> str:
        socks = self._socks()
        # 1) plain requests (+ optional socks)
        try:
            import requests
            session = requests.Session()
            session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            })
            proxies = {"http": socks, "https": socks} if socks else None
            for url in (DISCLAIMER_URL, PORTAL_URL):
                resp = session.get(url, timeout=25, proxies=proxies, verify=False)
                if resp.status_code == 200 and not self._is_incapsula(resp.text):
                    logger.info(f"Greenville: HTTP OK via {url}")
                    return resp.text
        except Exception as e:
            logger.debug(f"Greenville HTTP path failed: {e}")

        # 2) curl_cffi
        try:
            from curl_cffi import requests as cr
            proxies = {"http": socks, "https": socks} if socks else None
            resp = cr.get(
                PORTAL_URL,
                timeout=30,
                impersonate="chrome131",
                proxies=proxies,
                verify=False,
            )
            if resp.status_code == 200 and not self._is_incapsula(resp.text):
                logger.info("Greenville: curl_cffi OK")
                return resp.text
        except Exception as e:
            logger.debug(f"Greenville curl_cffi failed: {e}")

        # 3) DrissionPage (may still fail on Incapsula from DC IP)
        try:
            html = self._fetch_via_drission(socks)
            if html and not self._is_incapsula(html):
                logger.info("Greenville: DrissionPage OK")
                return html
        except Exception as e:
            logger.debug(f"Greenville DrissionPage failed: {e}")

        return ""

    def _fetch_via_drission(self, socks: Optional[str]) -> str:
        from DrissionPage import ChromiumPage, ChromiumOptions

        opts = ChromiumOptions()
        opts.headless(True)
        opts.set_argument("--no-sandbox")
        opts.set_argument("--disable-gpu")
        if socks:
            # Chrome proxy server format
            proxy = socks.replace("socks5://", "").replace("socks5h://", "")
            opts.set_argument(f"--proxy-server=socks5://{proxy}")
        # Prefer system Chrome when present
        for path in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ):
            if os.path.exists(path):
                opts.set_browser_path(path)
                break

        page = ChromiumPage(opts)
        try:
            page.get(DISCLAIMER_URL, timeout=45)
            time.sleep(3)
            # Accept disclaimer if button present
            for label in ("Accept", "I Agree", "I accept", "Continue", "Agree"):
                try:
                    el = page.ele(f"text:{label}", timeout=1)
                    if el:
                        el.click()
                        time.sleep(2)
                        break
                except Exception:
                    pass
            page.get(PORTAL_URL, timeout=45)
            for _ in range(10):
                time.sleep(2)
                html = page.html or ""
                if html and not self._is_incapsula(html) and len(html) > 1500:
                    return html
            return page.html or ""
        finally:
            try:
                page.quit()
            except Exception:
                pass

    def _letter_search(self) -> List[ArrestRecord]:
        """If form is accessible, walk last-name letters."""
        records: List[ArrestRecord] = []
        socks = self._socks()
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            return []

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        proxies = {"http": socks, "https": socks} if socks else None

        for letter in LETTERS:
            try:
                resp = session.get(PORTAL_URL, timeout=25, proxies=proxies, verify=False)
                if self._is_incapsula(resp.text):
                    break
                soup = BeautifulSoup(resp.text, "html.parser")
                form = soup.find("form")
                if not form:
                    break
                action = form.get("action") or PORTAL_URL
                action = urljoin(PORTAL_URL, action)
                data = {}
                for inp in form.find_all("input"):
                    name = inp.get("name")
                    if not name:
                        continue
                    typ = (inp.get("type") or "text").lower()
                    if typ in ("submit", "button"):
                        continue
                    data[name] = inp.get("value") or ""
                # Heuristic field names
                for key in list(data.keys()):
                    lk = key.lower()
                    if "last" in lk:
                        data[key] = letter
                    elif "first" in lk:
                        data[key] = ""
                # Submit buttons
                for inp in form.find_all("input", {"type": "submit"}):
                    if inp.get("name"):
                        data[inp["name"]] = inp.get("value") or "Search"
                        break
                else:
                    data["Search"] = "Search"

                resp = session.post(
                    action, data=data, timeout=30, proxies=proxies, verify=False
                )
                records.extend(self._parse_results_html(resp.text))
                time.sleep(0.4)
            except Exception as e:
                logger.debug(f"Greenville letter {letter}: {e}")
        return records

    @staticmethod
    def _is_incapsula(html: str) -> bool:
        if not html:
            return True
        low = html.lower()
        return (
            "incapsula" in low
            or "_incapsula_resource" in low
            or 'name="robots" content="noindex' in low
            or (len(html) < 1200 and "iframe" in low and "main-iframe" in low)
        )

    @staticmethod
    def _looks_like_search_form(html: str) -> bool:
        low = html.lower()
        return "last name" in low or "lastname" in low or "txtlast" in low

    def _parse_results_html(self, html: str) -> List[ArrestRecord]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return []
        if self._is_incapsula(html):
            return []

        soup = BeautifulSoup(html, "html.parser")
        records: List[ArrestRecord] = []

        # Table-based results
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [
                th.get_text(" ", strip=True).lower()
                for th in rows[0].find_all(["th", "td"])
            ]
            if not any(
                any(k in h for k in ("name", "inmate", "booking", "charge"))
                for h in headers
            ) and len(rows) < 3:
                continue
            for tr in rows[1:]:
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) < 2:
                    continue
                name = cells[0]
                if not name or len(name) < 2:
                    continue
                charges = "Unknown"
                bond = "0"
                booking = ""
                booked = ""
                for i, h in enumerate(headers):
                    if i >= len(cells):
                        break
                    if "charge" in h or "offense" in h:
                        charges = cells[i]
                    elif "bond" in h or "bail" in h:
                        bond = re.sub(r"[^\d.]", "", cells[i]) or "0"
                    elif "book" in h and "date" in h:
                        booked = cells[i]
                    elif "book" in h or "inmate #" in h or "number" in h:
                        booking = cells[i]
                if not booking:
                    booking = (
                        f"GVL_{re.sub(r'[^A-Za-z0-9]', '', name)[:16]}_"
                        f"{abs(hash(name + booked)) % 100000}"
                    )
                first = last = middle = ""
                if "," in name:
                    last, rest = [p.strip() for p in name.split(",", 1)]
                    parts = rest.split()
                    first = parts[0] if parts else ""
                    middle = " ".join(parts[1:]) if len(parts) > 1 else ""
                records.append(
                    ArrestRecord(
                        County=self.county,
                        State="SC",
                        Full_Name=name,
                        First_Name=first,
                        Middle_Name=middle,
                        Last_Name=last,
                        Booking_Number=str(booking),
                        Booking_Date=booked,
                        Charges=charges,
                        Bond_Amount=bond,
                        Status="In Custody",
                        Detail_URL=PORTAL_URL,
                        Facility="Greenville County Detention Center",
                    )
                )
            if records:
                break
        return records


GreenvilleScraper = GreenvilleSCScraper
