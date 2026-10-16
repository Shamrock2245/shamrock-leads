"""
Marion County (FL) Arrest Scraper — ASP.NET Recent Bookings (fail closed).
=========================================================================
Source: Marion County Sheriff's Office, https://jail.marionso.com/

Fail closed (2026-10-14, docs/recon/FL_RULE_CONFLICT_FAIL_CLOSED_2026-10-14.md):
the listing sits behind AWS WAF and was only reached through a residential
proxy with curl_cffi Chrome impersonation. Both are retired (2026-10-16): this
module has no proxy, no impersonation and no Chrome UA. The only fetch path is
plain_http with the honest UA and TLS verified, and it stays behind the
SOURCE_CONTRACT_VALIDATED guard until a plain direct public read is proven.

Recent Bookings columns (2026-10-07 recon): Booking #, Photo, Inmate ID, name
parts, DOB, Sex, Race, Booking Date, Release Date, InCustody. Status comes only
from the published InCustody / Release Date cells (status_source recorded);
otherwise "". Detail ``Charge Information``: charges verbatim; bond only when
every charge row publishes a dollar amount (a published $0 is kept), else "".
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from scrapers.base_scraper import BaseScraper
from scrapers.plain_http import HONEST_USER_AGENT, EgressBlocked, SourceTLSInvalid, make_session
from scrapers.plain_http import request as plain_request
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://jail.marionso.com"
SEARCH_URL = f"{BASE_URL}/"
FACILITY = "Marion County Jail"
MAX_DETAIL_FETCHES = 80  # polite bound per run
DETAIL_SLEEP_S = 0.4
_BLOCKS = (EgressBlocked, SourceTLSInvalid)

HEADERS = {
    "User-Agent": HONEST_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_URL,
}

STATUS_SOURCE_COLUMN = "published_in_custody_column"
STATUS_SOURCE_RELEASE_DATE = "published_release_date"


def status_fields(in_custody: str, release_date: str) -> Tuple[str, str]:
    """(Status, status_source) from the published InCustody / Release Date cells only."""
    v = (in_custody or "").strip().upper()
    if v in ("Y", "YES", "TRUE", "1"):
        return "In Custody", STATUS_SOURCE_COLUMN
    if v in ("N", "NO", "FALSE", "0"):
        return "Released", STATUS_SOURCE_COLUMN
    if (release_date or "").strip():
        return "Released", STATUS_SOURCE_RELEASE_DATE
    return "", ""


class MarionCountyScraper(BaseScraper):
    """Marion County (FL) — fail closed (2026-10-14).

    The listing sits behind AWS WAF and the module only reached it through a
    residential / Tailscale proxy with curl_cffi Chrome impersonation. Proxies
    and impersonation are not allowed, so no source request is made until a
    plain, direct public read is proven (docs/recon/FL_RULE_CONFLICT_FAIL_CLOSED_2026-10-14.md).
    ``parse_detail_charges`` stays for that future path and its tests.
    """

    SOURCE_CONTRACT_VALIDATED = False
    SOURCE_CONTRACT_REASON = (
        "Marion inmate lookup is AWS-WAF gated and was reached only via a residential "
        "proxy with TLS impersonation; proxies/impersonation are not allowed, so refuse "
        "emission until a plain direct read is proven."
    )

    @property
    def county(self) -> str:
        return "Marion"

    def scrape(self) -> List[ArrestRecord]:
        if not self.SOURCE_CONTRACT_VALIDATED:
            logger.warning("Marion: SOURCE_CONTRACT_VALIDATED=False — %s", self.SOURCE_CONTRACT_REASON)
            return []
        from bs4 import BeautifulSoup

        return self._scrape_with_session(make_session(HEADERS), BeautifulSoup)

    def _get(self, session: Any, method: str, url: str, **kw: Any):
        resp = plain_request(session, method, url, county="Marion", **kw)
        resp.raise_for_status()
        return resp

    def _scrape_with_session(self, session: Any, BeautifulSoup: Any) -> List[ArrestRecord]:
        """GET search form → POST Recent Bookings → parse table (plain_http only)."""
        resp = self._get(session, "GET", SEARCH_URL, timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")

        def _hidden(name: str) -> str:
            el = soup.find("input", {"name": name})
            return el["value"] if el and el.get("value") else ""

        post_data = {
            "__VIEWSTATE": _hidden("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": _hidden("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": _hidden("__EVENTVALIDATION"),
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "txtLname": "",
            "txtFName": "",
            "btnRecentBookings": "Recent",
        }
        resp2 = self._get(session, "POST", SEARCH_URL, data=post_data, timeout=60)
        rows = self.parse_recent_bookings(resp2.text)

        out: List[ArrestRecord] = []
        detail_fetches = 0
        for raw in rows:
            if raw["detail_url"] and detail_fetches < MAX_DETAIL_FETCHES:
                detail_fetches += 1
                try:
                    d = self._get(session, "GET", raw["detail_url"], timeout=30)
                    parsed = self.parse_detail_charges(d.text)
                    raw["charges"], raw["bond_amount"] = parsed["charges"], parsed["bond_amount"]
                except _BLOCKS:
                    raise
                except requests.RequestException as exc:
                    logger.debug("[Marion] detail failed: %s", type(exc).__name__)
                time.sleep(DETAIL_SLEEP_S)
            out.append(self.to_record(raw))
        return out

    @classmethod
    def parse_recent_bookings(cls, html: str) -> List[Dict[str, str]]:
        """Recent Bookings table → raw row dicts (no network)."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html or "", "html.parser")
        table = None
        for t in soup.find_all("table"):
            if any(k in t.get_text(" ").lower() for k in ("booking", "inmate")) and len(t.find_all("tr")) > 1:
                table = t
                break
        if table is None:
            return []
        out: List[Dict[str, str]] = []
        for row in table.find_all("tr")[1:]:
            texts = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(texts) < 11 or not any(texts):
                continue
            link = row.find("a", href=True)
            href = link["href"] if link else ""
            if href and not href.startswith("http"):
                href = f"{BASE_URL}/{href.lstrip('/')}"
            last, first, middle = texts[3], texts[4], texts[5]
            out.append({
                "booking_num": cls._clean(texts[0]),
                "inmate_id": texts[2],
                "full_name": f"{last}, {first} {middle}".strip().rstrip(","),
                "first_name": first, "middle_name": middle, "last_name": last,
                "dob": cls._clean(texts[7]),
                "sex": cls._clean(texts[8])[:1].upper(),
                "race": cls._clean(texts[9]),
                "booking_date": cls._clean(texts[10]),
                "release_date": cls._clean(texts[11]) if len(texts) > 11 else "",
                "in_custody": texts[12] if len(texts) > 12 else "",
                "charges": "", "bond_amount": "",
                "detail_url": href,
            })
        return out

    def to_record(self, raw: Dict[str, str]) -> ArrestRecord:
        status, source = status_fields(raw.get("in_custody", ""), raw.get("release_date", ""))
        return ArrestRecord(
            County=self.county, State="FL",
            Booking_Number=raw["booking_num"], Person_ID=raw["inmate_id"],
            Full_Name=raw["full_name"], First_Name=raw["first_name"],
            Middle_Name=raw["middle_name"], Last_Name=raw["last_name"],
            DOB=raw["dob"], Booking_Date=raw["booking_date"],
            Status=status,
            Release_Date=raw.get("release_date", "") if status == "Released" else "",
            Facility=FACILITY, Race=raw["race"], Sex=raw["sex"],
            Charges=raw.get("charges", ""), Bond_Amount=raw.get("bond_amount", ""),
            Detail_URL=raw["detail_url"], LastCheckedMode="INITIAL",
            extra_data={"status_source": source} if source else {},
        )

    @staticmethod
    def _clean(text: Any) -> str:
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    @classmethod
    def parse_detail_charges(cls, html: str) -> dict:
        """Details.aspx Charge Information → {charges, bond_amount}.

        Charges verbatim from the charge grid. Bond is the sum of the grid's
        bond cells only when every row publishes a dollar amount (a published
        $0 is kept as "0"); "No data was returned", NO BOND / HOLD / blank
        cells or no bond column give "" (unknown), never "0".
        """
        from bs4 import BeautifulSoup

        if not html or re.search(r"Charge Information.*?No data was returned", html, flags=re.I | re.S):
            return {"charges": "", "bond_amount": ""}
        soup = BeautifulSoup(html, "html.parser")
        charges: List[str] = []
        amounts: List[Optional[float]] = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            headers = [cls._clean(c.get_text(" ", strip=True)).lower() for c in rows[0].find_all(["th", "td"])]
            charge_idx = next((i for i, h in enumerate(headers) if "charge" in h or "offense" in h or "description" in h), None)
            bond_idx = next((i for i, h in enumerate(headers) if "bond" in h or "bail" in h), None)
            if charge_idx is None:
                continue
            for row in rows[1:]:
                cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
                if charge_idx >= len(cells) or not cls._clean(cells[charge_idx]):
                    continue
                charges.append(cls._clean(cells[charge_idx]))
                amounts.append(cls._parse_bond(cells[bond_idx]) if bond_idx is not None and bond_idx < len(cells) else None)
        bond = ""
        if amounts and all(a is not None for a in amounts):
            total = sum(amounts)
            bond = f"{total:.2f}".rstrip("0").rstrip(".") or "0"
        return {"charges": " | ".join(charges), "bond_amount": bond}

    @staticmethod
    def _parse_bond(bond_str: str) -> Optional[float]:
        """A published dollar amount (0 included), else None (unknown)."""
        m = re.fullmatch(r"\$?\s*([\d,]+(?:\.\d{1,2})?)", (bond_str or "").strip())
        if not m:
            return None
        return float(m.group(1).replace(",", ""))
