"""
Florence County (SC) Arrest Scraper.

Platform: ASP.NET / DevExpress grid at booking.fcso.org
Search via InmatesSearchBox + InmatesSearchButton postback (letter walk).

Listing rows expose name / age / race / sex / admit date and an
``inmate-details?id=&bid=`` link. Charge text, bond amount/type, arresting
agency, and the source-issued ``Name ID`` live on the detail page — the
listing never publishes them. Booking_Number is the detail ``Name ID`` only
(no FLO_ / name-hash keys). Rows without a parseable Name ID are skipped.

2026-10-15: honest identity, published-only bond, no invented custody.
Every request goes through scrapers.plain_http (honest User-Agent, certificate
verified); a 401/403/429, challenge page, dropped handshake or bad certificate
raises (egress_block: / tls_cert_invalid:) and fails the run instead of
returning an empty roster. Bond is the sum of the published per-charge Bond
cells only when every charge publishes one (blank / "No Bond" -> ""; a
published $0.00 is kept); never "0" by default. Status is "": the roster is
the county's "Current" page (released people are on separate *-released
pages), but there is no published population figure to check the count against,
so custody-only is not proven to the Orange standard. See
docs/recon/SC_FLORENCE_ROSTER_SCOPE_2026-10-15.md.
"""
from __future__ import annotations

import logging
import re
import string
import time
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from scrapers.plain_http import HONEST_USER_AGENT, SourceTLSInvalid
from scrapers.plain_http import request as plain_request
from scrapers.published_bond import charge_bond_amount, total_published_bond
from scrapers.scraper_resilience import EgressBlocked
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PORTAL_URL = "https://booking.fcso.org/index"
ORIGIN = "https://booking.fcso.org"
REQUEST_PAUSE_S = 0.3
DETAIL_PAUSE_S = 0.25
MAX_DETAIL_FETCHES = 800
_NAME_ID = re.compile(r"^\d{2,}$")


_BLOCKS = (EgressBlocked, SourceTLSInvalid)


class FlorenceContractError(RuntimeError):
    """The roster no longer matches the verified contract (every letter failed)."""


class _HonestSession(requests.Session):
    """requests.Session whose every call goes through plain_http (honest UA, verify on, typed blocks)."""

    def __init__(self):
        super().__init__()
        self.headers.update({"User-Agent": HONEST_USER_AGENT})
        self.verify = True

    def request(self, method, url, **kwargs):  # noqa: D401
        sess = self

        class _Raw:
            def request(self, m, u, **kw):
                return requests.Session.request(sess, m, u, **kw)

        return plain_request(_Raw(), method, url, county="Florence", **kwargs)


class FlorenceScraper(BaseScraper):
    SOURCE_CONTRACT_VALIDATED = True
    SOURCE_CONTRACT_REASON = (
        "booking.fcso.org DevExpress letter-walk roster + inmate-details; "
        "source-issued Name ID is Booking_Number; charges/bond/bond type from "
        "detail Charge grid only (never invented)."
    )

    @property
    def county(self) -> str:
        return "Florence"

    @property
    def state(self) -> str:
        return "SC"

    def scrape(self) -> List[ArrestRecord]:
        start = time.time()
        session = _HonestSession()
        session.headers.update({
            "Referer": PORTAL_URL,
            "Origin": ORIGIN,
        })

        seen_list: Set[str] = set()
        roster: List[dict] = []

        failed_letters = 0
        for letter in string.ascii_uppercase:
            try:
                rows = self._search_letter(session, letter)
            except _BLOCKS:
                raise  # blocked or bad certificate: fail visibly, never an empty success
            except Exception as e:
                failed_letters += 1
                logger.warning("Florence letter %s: %s", letter, type(e).__name__)
                continue
            for row in rows:
                key = row.get("detail_url") or (
                    f"{row['name']}|{row.get('booked','')}|{row.get('age','')}"
                )
                if key in seen_list:
                    continue
                seen_list.add(key)
                roster.append(row)
            time.sleep(REQUEST_PAUSE_S)
        if failed_letters == len(string.ascii_uppercase):
            raise FlorenceContractError("Florence: every letter search failed")

        records: List[ArrestRecord] = []
        seen_booking: Set[str] = set()
        detail_fetches = 0
        for row in roster:
            detail: Dict[str, str] = {}
            detail_url = row.get("detail_url") or ""
            if detail_url and detail_fetches < MAX_DETAIL_FETCHES:
                try:
                    detail = self._parse_detail(session, detail_url)
                    detail_fetches += 1
                    time.sleep(DETAIL_PAUSE_S)
                except _BLOCKS:
                    raise
                except Exception as e:
                    logger.debug("Florence detail %s: %s", detail_url, e)
            rec = self._to_record(row, detail)
            if not rec:
                continue
            if rec.Booking_Number in seen_booking:
                continue
            seen_booking.add(rec.Booking_Number)
            records.append(rec)

        logger.info(
            "Florence: %d records (%d roster / %d details) in %.1fs",
            len(records),
            len(roster),
            detail_fetches,
            time.time() - start,
        )
        return records

    def _search_letter(self, session: requests.Session, letter: str) -> List[dict]:
        resp = session.get(PORTAL_URL, timeout=25)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        data = self._all_fields(soup)
        if not data.get("__VIEWSTATE"):
            raise RuntimeError("missing ViewState")

        data["InmatesSearchBox"] = letter
        data["__EVENTTARGET"] = "InmatesSearchButton"
        data["__EVENTARGUMENT"] = ""
        if "ChargesSearchBox" in data:
            data["ChargesSearchBox"] = ""
        if "DaysSearchBox" in data:
            data["DaysSearchBox"] = ""

        resp = session.post(PORTAL_URL, data=data, timeout=40)
        resp.raise_for_status()
        return self._parse_grid(BeautifulSoup(resp.text, "html.parser"))

    @staticmethod
    def _all_fields(soup: BeautifulSoup) -> Dict[str, str]:
        data: Dict[str, str] = {}
        for inp in soup.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            typ = (inp.get("type") or "text").lower()
            if typ in ("submit", "button", "image"):
                continue
            data[name] = inp.get("value") or ""
        return data

    def _parse_grid(self, soup: BeautifulSoup) -> List[dict]:
        rows_out: List[dict] = []
        data_rows = soup.find_all("tr", class_=re.compile(r"dxgvDataRow|DataRow", re.I))
        if not data_rows:
            grid = soup.find("table", id=re.compile(r"gvInmates", re.I))
            if grid:
                data_rows = grid.find_all("tr")[1:]

        for tr in data_rows:
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 2:
                continue
            name = cells[0]
            if not name or not re.search(r"[A-Za-z]", name):
                continue
            if name.lower() in ("name", "inmate", "full name"):
                continue
            detail_url = ""
            link = tr.find("a", href=re.compile(r"inmate-details", re.I))
            if link and link.get("href"):
                detail_url = urljoin(ORIGIN + "/", link["href"])
            rows_out.append({
                "name": name,
                "age": cells[1] if len(cells) > 1 else "",
                "race": cells[2] if len(cells) > 2 else "",
                "sex": cells[3] if len(cells) > 3 else "",
                "booked": cells[4] if len(cells) > 4 else "",
                "detail_url": detail_url,
            })
        return rows_out

    def _parse_detail(self, session: requests.Session, url: str) -> Dict[str, str]:
        resp = session.get(url, timeout=30, headers={"Referer": PORTAL_URL})
        resp.raise_for_status()
        return self.parse_detail_html(resp.text)

    @classmethod
    def parse_detail_html(cls, html: str) -> Dict[str, str]:
        """Extract Name ID, charges, and bond fields from an inmate-details page."""
        soup = BeautifulSoup(html, "html.parser")
        info: Dict[str, str] = {}

        for td in soup.find_all("td"):
            label = td.get_text(" ", strip=True).rstrip(":")
            nxt = td.find_next_sibling("td")
            if not nxt:
                continue
            value = nxt.get_text(" ", strip=True)
            if not value:
                continue
            label_l = label.lower()
            if label_l == "name id" and _NAME_ID.match(value):
                info["name_id"] = value
            elif label_l == "admit date":
                info["admit"] = value
            elif label_l == "age":
                info["age"] = value
            elif label_l == "race":
                info["race"] = value
            elif label_l in ("sex", "gender"):
                info["sex"] = value[:1].upper()

        charges: List[str] = []
        charge_bonds: List[Optional[float]] = []  # published per-charge amounts; None = unknown
        bond_types: List[str] = []
        agencies: List[str] = []

        for tr in soup.find_all("tr", class_=re.compile(r"dxgvDataRow", re.I)):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if not cells:
                continue
            charge = cells[0].strip()
            if not charge or "drag a column" in charge.lower():
                continue
            if charge.lower() in ("charge", "count=1", "loading…", "loading..."):
                continue
            new_charge = charge not in charges
            if new_charge:
                charges.append(charge)

            # Column order observed 2026-10-07:
            # Charge | Offense Date | Court Type | Bond | Bond Type | Arresting Agency
            bond_cell = cells[3] if len(cells) > 3 else ""
            bond_type_cell = cells[4] if len(cells) > 4 else ""
            agency_cell = cells[5] if len(cells) > 5 else ""

            if new_charge:
                charge_bonds.append(charge_bond_amount(bond_cell))

            if bond_type_cell and bond_type_cell not in bond_types:
                bond_types.append(bond_type_cell)
            if agency_cell and agency_cell not in agencies:
                agencies.append(agency_cell)

        if charges:
            info["charges"] = " | ".join(charges)
        bond = total_published_bond(charge_bonds)  # "" unless every charge publishes one
        if bond:
            info["bond"] = bond
        if bond_types:
            info["bond_type"] = " | ".join(bond_types)
        if agencies:
            info["agency"] = agencies[0]
        return info

    def _to_record(self, row: dict, detail: Dict[str, str]) -> Optional[ArrestRecord]:
        name_id = (detail.get("name_id") or "").strip()
        if not name_id or not _NAME_ID.match(name_id):
            # No invented FLO_ keys — detail Name ID is required.
            return None

        name = row["name"]
        first = last = middle = ""
        if "," in name:
            last, rest = [p.strip() for p in name.split(",", 1)]
            parts = rest.split()
            first = parts[0] if parts else ""
            middle = " ".join(parts[1:]) if len(parts) > 1 else ""
        else:
            parts = name.split()
            first = parts[0] if parts else ""
            last = parts[-1] if len(parts) > 1 else name

        booked = detail.get("admit") or row.get("booked") or ""
        charges = detail.get("charges") or ""
        bond = detail.get("bond") or ""  # unknown stays ""; a published "0" is kept
        bond_type = detail.get("bond_type") or ""

        return ArrestRecord(
            County=self.county,
            State="SC",
            Full_Name=name,
            First_Name=first,
            Middle_Name=middle,
            Last_Name=last,
            Booking_Number=name_id,
            Person_ID=name_id,
            Booking_Date=booked,
            Arrest_Date=booked,
            Age_At_Arrest=str(detail.get("age") or row.get("age") or ""),
            Race=str(detail.get("race") or row.get("race") or ""),
            Sex=(detail.get("sex") or row.get("sex") or "")[:1].upper(),
            Charges=charges,
            Bond_Amount=bond,
            Bond_Type=bond_type,
            Agency=detail.get("agency") or "",
            Status="",  # custody-only not proven (no population figure); see module docstring
            Detail_URL=row.get("detail_url") or PORTAL_URL,
            Facility="Florence County Detention Center",
        )
