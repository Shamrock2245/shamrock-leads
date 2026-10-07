"""
Marion County Arrest Scraper — ASP.NET Recent Bookings via residential egress
=============================================================================
Source: Marion County Sheriff's Office
URL: https://jail.marionso.com/
Method: curl_cffi Chrome JA3 + Warren/Tailscale residential proxy

AWS WAF blocks Hetzner VPS IPs with HTTP 403. Direct egress will always fail.
This scraper **requires** validated US residential exit (APE Warren preferred,
Tailscale/office SOCKS fallback) — same stack as Charlotte/Manatee CF counties.

Recent Bookings roster columns (2026-10-07 recon): Booking #, Photo, Inmate ID,
name parts, DOB, Sex, Race, Booking Date, Release Date, InCustody — **no**
charges or bond on the list. Detail pages (`Details.aspx?InmateRID=…`) expose a
"Charge Information" section; when the jail returns charge rows we parse
offense text + any published bond dollars. When the section says
"No data was returned" we leave Charges="" and Bond_Amount="0" (no invention).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://jail.marionso.com"
SEARCH_URL = f"{BASE_URL}/"
FACILITY = "Marion County Jail"
IMPERSONATE = "chrome131"
MAX_PROXY_ATTEMPTS = 4
MAX_DETAIL_FETCHES = 80  # polite bound per run
DETAIL_SLEEP_S = 0.4

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": BASE_URL,
    "DNT": "1",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}


class MarionCountyScraper(BaseScraper):
    """Marion County (FL) — AWS WAF; residential egress required."""

    @property
    def county(self) -> str:
        return "Marion"

    def scrape(self) -> List[ArrestRecord]:
        try:
            from curl_cffi import requests as cffi_requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("curl_cffi/bs4 not installed")
            raise

        from scrapers.socks_proxy import (
            curl_cffi_proxies,
            resolve_residential_proxy,
        )

        last_err: Optional[Exception] = None
        t0 = time.time()

        for attempt in range(1, MAX_PROXY_ATTEMPTS + 1):
            sticky = "fl-marion" if attempt == 1 else f"fl-marion-r{attempt}"
            try:
                proxy_url, proxy_source = resolve_residential_proxy(
                    self,
                    require=True,
                    sticky_session=sticky,
                    max_ape_attempts=3,
                )
            except RuntimeError as exc:
                logger.error("[Marion] no residential egress: %s", exc)
                raise

            logger.info(
                "[Marion] attempt %d/%d via %s",
                attempt,
                MAX_PROXY_ATTEMPTS,
                proxy_source,
            )
            proxies = curl_cffi_proxies(proxy_url)
            session = cffi_requests.Session()
            try:
                records = self._scrape_with_session(
                    session, BeautifulSoup, proxies=proxies
                )
                if proxy_source == "ape" and proxy_url:
                    self.record_proxy_success(
                        proxy_url, (time.time() - t0) * 1000
                    )
                logger.info(
                    "[Marion] %d records (proxy=%s)",
                    len(records),
                    proxy_source,
                )
                return records
            except _WafBlocked as exc:
                last_err = exc
                logger.warning(
                    "[Marion] WAF/403 via %s — rotating residential exit",
                    proxy_source,
                )
                if proxy_url and proxy_source == "ape":
                    self.record_proxy_failure(proxy_url)
                time.sleep(1.5 * attempt)
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "[Marion] scrape failed via %s: %s", proxy_source, exc
                )
                if proxy_url and proxy_source == "ape":
                    self.record_proxy_failure(proxy_url)
                time.sleep(1.0 * attempt)
            finally:
                try:
                    session.close()
                except Exception:
                    pass

        raise RuntimeError(
            f"Marion: all residential egress attempts failed "
            f"(last={last_err})"
        )

    def _scrape_with_session(
        self,
        session: Any,
        BeautifulSoup: Any,
        *,
        proxies: Optional[Dict[str, str]],
    ) -> List[ArrestRecord]:
        """GET search form → POST Recent Bookings → parse table."""
        resp = session.get(
            SEARCH_URL,
            headers=HEADERS,
            timeout=30,
            impersonate=IMPERSONATE,
            proxies=proxies,
        )
        if resp.status_code == 403:
            raise _WafBlocked(f"GET HTTP 403 (WAF)")
        if resp.status_code != 200:
            raise RuntimeError(f"GET HTTP {resp.status_code}")
        if "Inmate Inquiry" not in (resp.text or "") and "VIEWSTATE" not in (
            resp.text or ""
        ):
            # WAF interstitial or empty shell
            if resp.status_code == 200 and len(resp.text or "") < 500:
                raise _WafBlocked("GET short body — likely WAF")
        time.sleep(1.0)

        soup = BeautifulSoup(resp.text, "html.parser")

        def _get_hidden(name: str) -> str:
            el = soup.find("input", {"name": name})
            return el["value"] if el and el.get("value") else ""

        # Empty btnSearch returns 500; dedicated "Recent" button works.
        post_data = {
            "__VIEWSTATE": _get_hidden("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _get_hidden("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": _get_hidden("__EVENTVALIDATION"),
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "txtLname": "",
            "txtFName": "",
            "btnRecentBookings": "Recent",
        }

        resp2 = session.post(
            SEARCH_URL,
            data=post_data,
            headers=HEADERS,
            timeout=60,
            impersonate=IMPERSONATE,
            proxies=proxies,
        )
        if resp2.status_code == 403:
            raise _WafBlocked("POST HTTP 403 (WAF)")
        if resp2.status_code != 200:
            raise RuntimeError(f"POST HTTP {resp2.status_code}")

        soup2 = BeautifulSoup(resp2.text, "html.parser")
        records: List[dict] = []

        table = None
        for t in soup2.find_all("table"):
            header_text = t.get_text(" ").lower()
            if any(
                kw in header_text
                for kw in ("name", "booking", "inmate", "arrest")
            ):
                rows = t.find_all("tr")
                if len(rows) > 1:
                    table = t
                    break

        if not table:
            logger.warning("[Marion] no data table found")
            return []

        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            if not any(texts):
                continue

            # Booking#, Photo, InmateID, Last, First, Mid, Suffix, DOB, Sex, Race, BookingDate, Release, InCustody
            booking_num = texts[0] if len(texts) > 0 else ""
            inmate_id = texts[2] if len(texts) > 2 else ""
            last_name = texts[3] if len(texts) > 3 else ""
            first_name = texts[4] if len(texts) > 4 else ""
            middle_name = texts[5] if len(texts) > 5 else ""
            dob = texts[7] if len(texts) > 7 else ""
            sex = texts[8] if len(texts) > 8 else ""
            race = texts[9] if len(texts) > 9 else ""
            booking_date = texts[10] if len(texts) > 10 else ""
            in_custody = texts[12] if len(texts) > 12 else "Y"
            full_name = f"{last_name}, {first_name} {middle_name}".strip().rstrip(
                ","
            )

            detail_url = ""
            link = row.find("a", href=True)
            if link:
                href = link["href"]
                if not href.startswith("http"):
                    href = f"{BASE_URL}/{href.lstrip('/')}"
                detail_url = href

            status = (
                "In Custody"
                if str(in_custody).upper() in ("Y", "YES", "1")
                else "Released"
            )

            charges = ""
            bond_amount = "0"
            records.append(
                {
                    "booking_num": self._clean(booking_num),
                    "inmate_id": inmate_id,
                    "full_name": full_name,
                    "first_name": first_name,
                    "middle_name": middle_name,
                    "last_name": last_name,
                    "dob": self._clean(dob),
                    "booking_date": self._clean(booking_date),
                    "status": status,
                    "race": self._clean(race),
                    "sex": self._clean(sex)[:1].upper() if sex else "",
                    "charges": charges,
                    "bond_amount": bond_amount,
                    "detail_url": detail_url,
                }
            )

        # Phase 2: detail pages for charges/bond when the jail publishes them.
        out: List[ArrestRecord] = []
        detail_fetches = 0
        for raw in records:
            if (
                raw.get("detail_url")
                and detail_fetches < MAX_DETAIL_FETCHES
            ):
                try:
                    dresp = session.get(
                        raw["detail_url"],
                        headers=HEADERS,
                        timeout=30,
                        impersonate=IMPERSONATE,
                        proxies=proxies,
                    )
                    detail_fetches += 1
                    if dresp.status_code == 403:
                        raise _WafBlocked("DETAIL HTTP 403 (WAF)")
                    if dresp.status_code == 200:
                        parsed = self.parse_detail_charges(dresp.text)
                        if parsed.get("charges"):
                            raw["charges"] = parsed["charges"]
                        raw["bond_amount"] = parsed.get("bond_amount") or "0"
                    time.sleep(DETAIL_SLEEP_S)
                except _WafBlocked:
                    raise
                except Exception as exc:
                    logger.debug(
                        "[Marion] detail %s: %s", raw.get("booking_num"), exc
                    )

            out.append(
                ArrestRecord(
                    County=self.county,
                    State="FL",
                    Booking_Number=raw["booking_num"],
                    Person_ID=raw["inmate_id"],
                    Full_Name=raw["full_name"],
                    First_Name=raw["first_name"],
                    Middle_Name=raw["middle_name"],
                    Last_Name=raw["last_name"],
                    DOB=raw["dob"],
                    Booking_Date=raw["booking_date"],
                    Status=raw["status"],
                    Release_Date="",
                    Facility=FACILITY,
                    Race=raw["race"],
                    Sex=raw["sex"],
                    Charges=raw["charges"],
                    Bond_Amount=self._format_bond_amount(raw["bond_amount"]),
                    Detail_URL=raw["detail_url"],
                    LastCheckedMode="INITIAL",
                )
            )

        return out

    @staticmethod
    def _clean(text: Any) -> str:
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    @classmethod
    def parse_detail_charges(cls, html: str) -> dict:
        """Parse Marion Details.aspx Charge Information (source-faithful).

        Returns charges + bond_amount. If the jail says "No data was returned"
        or publishes no bond dollars, bond_amount is "0".
        """
        if not html:
            return {"charges": "", "bond_amount": "0"}

        if re.search(
            r"Charge Information.*?No data was returned",
            html,
            flags=re.I | re.S,
        ):
            return {"charges": "", "bond_amount": "0"}

        try:
            from bs4 import BeautifulSoup
        except ImportError:
            BeautifulSoup = None

        charges: list[str] = []
        total = 0.0
        saw_bond = False

        if BeautifulSoup is not None:
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text("\n", strip=True)
            for m in re.finditer(
                r"(?:Offense|Charge Description|Description)\s*:?\s*([^\n]+)",
                text,
                flags=re.I,
            ):
                val = cls._clean(m.group(1))
                low = val.lower()
                if not val or low in {"information", "no data", "bond", "amount", "charge"}:
                    continue
                if val not in charges:
                    charges.append(val)
            for m in re.finditer(
                r"(?:Bond(?:\s*Amount)?|Total\s*Bond|Bail)\s*:?\s*"
                r"(\$?[\d,]+(?:\.\d{2})?|NO\s*BOND|N/?A|HOLD)",
                text,
                flags=re.I,
            ):
                saw_bond = True
                total += cls._parse_bond(m.group(1))

            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                if not rows:
                    continue
                headers = [
                    cls._clean(c.get_text(" ", strip=True)).lower()
                    for c in rows[0].find_all(["th", "td"])
                ]
                if not headers:
                    continue
                charge_idx = next(
                    (
                        i
                        for i, h in enumerate(headers)
                        if "charge" in h or "offense" in h or "description" in h
                    ),
                    None,
                )
                bond_idx = next(
                    (i for i, h in enumerate(headers) if "bond" in h or "bail" in h),
                    None,
                )
                if charge_idx is None and bond_idx is None:
                    continue
                for row in rows[1:]:
                    cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
                    if charge_idx is not None and charge_idx < len(cells):
                        val = cls._clean(cells[charge_idx])
                        if val and val not in charges:
                            charges.append(val)
                    if bond_idx is not None and bond_idx < len(cells):
                        saw_bond = True
                        total += cls._parse_bond(cells[bond_idx])

        if not saw_bond:
            for m in re.finditer(
                r"bond[^$\d]{0,40}(\$[\d,]+(?:\.\d{2})?)",
                html,
                flags=re.I,
            ):
                saw_bond = True
                total += cls._parse_bond(m.group(1))

        return {
            "charges": " | ".join(charges),
            "bond_amount": cls._format_bond_amount(total) if saw_bond else "0",
        }

    @staticmethod
    def _parse_bond(bond_str: str) -> float:
        if not bond_str:
            return 0.0
        cleaned = re.sub(r"[$,\s]", "", str(bond_str).strip().upper())
        if any(t in cleaned for t in ("NOBOND", "NONE", "N/A", "HOLD", "ROR")):
            return 0.0
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0

    @classmethod
    def _format_bond_amount(cls, value) -> str:
        if value is None or value == "":
            return "0"
        if isinstance(value, (int, float)):
            amount = float(value)
        else:
            amount = cls._parse_bond(str(value))
        if amount <= 0:
            return "0"
        if amount.is_integer():
            return str(int(amount))
        return f"{amount:.2f}"


class _WafBlocked(RuntimeError):
    """Raised when Marion AWS WAF returns 403 / interstitial on this exit IP."""
