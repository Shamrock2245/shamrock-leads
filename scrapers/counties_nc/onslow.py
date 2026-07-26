"""
Onslow County (NC) Arrest Scraper — P2C / CentralSquare (FingerprintJS gated).

Portal: https://p2c.ocsheriff.com/p2c/jailinmates.aspx

Onslow wraps classic P2C behind FingerprintJS (tr_uuid + fp=). Pure HTTP often
lands on a parking/sinkhole host (ww17.*). Strategy:

1. curl_cffi Chrome impersonation + fingerprint redirect chain → jqHandler
2. DrissionPage browser fallback (real JS FingerprintJS)
3. Fail closed with empty list + loud log (no fabricated records)

Note: County also pushes inmates via mobile app only for some consumers;
web P2C remains the only machine-readable public roster when reachable.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import List
from urllib.parse import urljoin

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

PORTAL_URL = "https://p2c.ocsheriff.com/p2c/jailinmates.aspx"
PORTAL_HTTP = "http://p2c.ocsheriff.com/p2c/jailinmates.aspx"
FACILITY = "Onslow County Detention Center"
PAGE_SIZE = 50
MAX_PAGES = 60

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}


class OnslowScraper(BaseScraper):
    @property
    def county(self) -> str:
        return "Onslow"

    @property
    def state(self) -> str:
        return "NC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        records: List[ArrestRecord] = []

        try:
            records = self._scrape_http()
        except Exception as e:
            logger.warning(f"Onslow HTTP path failed: {e}")

        if not records:
            try:
                records = self._scrape_browser()
            except Exception as e:
                logger.warning(f"Onslow browser path failed: {e}")

        if not records:
            logger.error(
                "Onslow: no roster rows — FingerprintJS/WAF or app-only portal. "
                "P2C URL may sinkhole to parking host after fp challenge."
            )

        logger.info(f"✅ Onslow (NC): {len(records)} records in {time.time() - start:.1f}s")
        return records

    def _scrape_http(self) -> List[ArrestRecord]:
        """Fingerprint challenge + jqGrid via curl_cffi when available."""
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            import requests as cffi_requests  # type: ignore

            session = cffi_requests.Session()
            session.headers.update(HEADERS)
            get = session.get
            post = session.post
            use_impersonate = False
        else:
            session = cffi_requests.Session(impersonate="chrome131")
            get = session.get
            post = session.post
            use_impersonate = True

        # Keep timeouts short — Onslow P2C frequently dead/sinkholes
        land_url = PORTAL_HTTP
        r = get(land_url, timeout=15, allow_redirects=True)
        html = r.text or ""
        base_page_url = str(getattr(r, "url", land_url))

        # Follow FingerprintJS redirect_link if present
        m = re.search(r"redirect_link\s*=\s*'([^']+)'", html)
        if m:
            fp = uuid.uuid4().hex[:16]
            challenge = m.group(1)
            if not challenge.endswith(("&", "?", "=")):
                challenge += "&" if "?" in challenge else "?"
            target = challenge + f"fp={fp}"
            r2 = get(target, timeout=15, allow_redirects=True)
            html = r2.text or ""
            base_page_url = str(getattr(r2, "url", target))
            # If still fingerprint wall, try fp=-7 once
            if "FingerprintJS" in html or "redirect_link" in html:
                m2 = re.search(r"redirect_link\s*=\s*'([^']+)'", html)
                if m2:
                    t2 = m2.group(1) + "fp=-7"
                    r3 = get(t2, timeout=15, allow_redirects=True)
                    html = r3.text or ""
                    base_page_url = str(getattr(r3, "url", t2))

        # Reject parking / sinkhole pages
        if self._is_dead_page(html, base_page_url):
            logger.warning(
                f"Onslow: dead/parking page after challenge ({base_page_url[:80]})"
            )
            return []

        if "jqGrid" not in html and "jqHandler" not in html and "__VIEWSTATE" not in html:
            logger.warning("Onslow: no P2C markers after challenge")
            return []

        api = urljoin(base_page_url, "jqHandler.ashx?op=s")
        ajax = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": base_page_url,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

        records: List[ArrestRecord] = []
        seen: set = set()
        page = 1
        total_pages = 1
        while page <= total_pages and page <= MAX_PAGES:
            payload = {
                "_search": "false",
                "nd": str(int(time.time() * 1000)),
                "rows": str(PAGE_SIZE),
                "page": str(page),
                "sidx": "",
                "sord": "asc",
                "t": "ii",
            }
            try:
                resp = post(api, data=payload, headers=ajax, timeout=40)
                data = resp.json()
            except Exception as e:
                logger.debug(f"Onslow jq page {page}: {e}")
                break
            try:
                total_pages = max(1, int(data.get("total") or 1))
            except (TypeError, ValueError):
                total_pages = 1
            rows = data.get("rows") or []
            if not rows:
                break
            for row in rows:
                rec = self._row_to_record(row)
                if not rec:
                    continue
                key = rec.Booking_Number or rec.Full_Name
                if key in seen:
                    continue
                seen.add(key)
                records.append(rec)
            page += 1
            time.sleep(0.2)

        logger.debug(
            f"Onslow HTTP (impersonate={use_impersonate}): {len(records)} rows"
        )
        return records

    def _scrape_browser(self) -> List[ArrestRecord]:
        try:
            from DrissionPage import ChromiumPage
        except Exception as e:
            logger.error(f"Onslow: DrissionPage unavailable: {e}")
            return []

        page = None
        try:
            co = self._get_browser_options() if hasattr(self, "_get_browser_options") else None
            page = ChromiumPage(co) if co else ChromiumPage()
            page.get(PORTAL_URL)
            time.sleep(4)  # allow FingerprintJS redirect
            html = page.html or ""
            if self._is_dead_page(html, page.url):
                return []
            # Try to drive search if form present
            for sel in ("#txtLName", "input[name=txtLName]", "input[id*=LName]"):
                el = page.ele(sel, timeout=1)
                if el:
                    el.input("A")
                    break
            # Parse any tables / intercept isn't available — re-use HTTP session from cookies
            # if jq grid loaded data into DOM
            records = self._parse_html_tables(html)
            if records:
                return records
            # Last resort: re-run jqHandler with browser cookies via requests
            return self._jq_with_browser_cookies(page)
        finally:
            try:
                if page:
                    page.quit()
            except Exception:
                pass

    def _jq_with_browser_cookies(self, page) -> List[ArrestRecord]:
        try:
            import requests
        except ImportError:
            return []
        session = requests.Session()
        session.headers.update(HEADERS)
        try:
            for c in page.cookies():
                if isinstance(c, dict) and c.get("name"):
                    session.cookies.set(c["name"], c.get("value", ""), domain=c.get("domain"))
        except Exception:
            pass
        base = page.url or PORTAL_URL
        api = urljoin(base, "jqHandler.ashx?op=s")
        ajax = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": base,
        }
        records: List[ArrestRecord] = []
        seen: set = set()
        for page_n in range(1, MAX_PAGES + 1):
            payload = {
                "_search": "false",
                "nd": str(int(time.time() * 1000)),
                "rows": str(PAGE_SIZE),
                "page": str(page_n),
                "sidx": "",
                "sord": "asc",
                "t": "ii",
            }
            try:
                resp = session.post(api, data=payload, headers=ajax, timeout=30)
                data = resp.json()
            except Exception:
                break
            rows = data.get("rows") or []
            if not rows:
                break
            for row in rows:
                rec = self._row_to_record(row)
                if not rec:
                    continue
                key = rec.Booking_Number or rec.Full_Name
                if key in seen:
                    continue
                seen.add(key)
                records.append(rec)
            total = int(data.get("total") or 1)
            if page_n >= total:
                break
            time.sleep(0.2)
        return records

    def _parse_html_tables(self, html: str) -> List[ArrestRecord]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        records: List[ArrestRecord] = []
        seen: set = set()
        for table in soup.find_all("table"):
            for tr in table.find_all("tr")[1:]:
                cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
                if len(cells) < 2:
                    continue
                name = next((c for c in cells if "," in c and len(c) > 4), "")
                if not name:
                    continue
                booking = next((c for c in cells if re.match(r"^\d{4,}$", c)), "")
                if not booking:
                    booking = f"ONS_{re.sub(r'[^A-Za-z0-9]', '', name)[:14]}"
                if booking in seen:
                    continue
                seen.add(booking)
                first, middle, last = self._pn(re.sub(r"\s*\([^)]*\)\s*$", "", name))
                records.append(
                    ArrestRecord(
                        County=self.county,
                        State="NC",
                        Full_Name=name,
                        First_Name=first,
                        Middle_Name=middle,
                        Last_Name=last,
                        Booking_Number=booking,
                        Charges="Unknown",
                        Bond_Amount="0",
                        Status="In Custody",
                        Facility=FACILITY,
                        Detail_URL=PORTAL_URL,
                        LastCheckedMode="INITIAL",
                    )
                )
        return records

    def _row_to_record(self, row: dict) -> ArrestRecord | None:
        if not isinstance(row, dict):
            return None
        last = (row.get("lastname") or "").strip()
        first = (row.get("firstname") or "").strip()
        middle = (row.get("middlename") or "").strip()
        disp = (row.get("disp_name") or "").strip()
        if disp and not last:
            clean = re.sub(r"\s*\([^)]*\)\s*$", "", disp).strip()
            first, middle, last = self._pn(clean)
        if not last and not first and not disp:
            return None
        if last and first:
            name = f"{last}, {first}" + (f" {middle}" if middle else "")
        else:
            name = re.sub(r"\s*\([^)]*\)\s*$", "", disp).strip()

        booking = str(row.get("book_id") or row.get("invid") or "").strip()
        if not booking:
            booking = f"ONS_{re.sub(r'[^A-Za-z0-9]', '', name)[:14]}"

        charges = (row.get("disp_charge") or row.get("chrgdesc") or "Unknown")
        book_date = str(row.get("disp_arrest_date") or row.get("date_arr") or "").split()[0]

        return ArrestRecord(
            County=self.county,
            State="NC",
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=booking,
            Booking_Date=book_date,
            Charges=str(charges).strip() or "Unknown",
            Bond_Amount="0",
            Status="In Custody",
            Facility=FACILITY,
            Detail_URL=PORTAL_URL,
            LastCheckedMode="INITIAL",
        )

    @staticmethod
    def _is_dead_page(html: str, url: str) -> bool:
        u = (url or "").lower()
        if "ww17." in u or "parking" in u:
            return True
        if not html or len(html) < 500:
            return True
        # consentmanager parking shells
        if "cmp_cdid" in html and "jqGrid" not in html and "jailinmate" not in html.lower():
            if "FingerprintJS" not in html:  # challenge page is ok to continue from
                return True
        return False

    @staticmethod
    def _pn(n: str):
        n = " ".join((n or "").strip().split())
        if "," in n:
            last, rest = n.split(",", 1)
            p = rest.strip().split()
            return (p[0] if p else ""), (" ".join(p[1:]) if len(p) > 1 else ""), last.strip()
        p = n.split()
        return (
            (p[0] if p else ""),
            (" ".join(p[1:-1]) if len(p) > 2 else ""),
            (p[-1] if len(p) > 1 else n),
        )
