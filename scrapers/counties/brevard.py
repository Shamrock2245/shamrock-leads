"""
Brevard County (FL) Arrest Scraper — BCSO Inmate Search (ASP.NET Core Razor Pages).

Source contract (recon 2026-10-07, docs/recon/FL_BREVARD_NEWWORLD_2026-10-07.md):
  * Portal: https://inmatesearch.brevardsheriff.org/ — ordinary public HTTPS,
    plain ``requests`` with TLS verification on (curl_cffi / verify=False /
    DrissionPage paths retired).
  * Search: GET ``/`` for ``__RequestVerificationToken`` and the ``max`` date
    the form allows (the source publishes through the previous day), then POST
    ``/?handler=Search`` with From/To dates → 302 → ``/Results?FromDate=&ToDate=``.
  * Results table header: ``Booking # | Name | DOB | Booking Date | Released``.
    Columns are mapped by header text. Booking_Number = source ``Booking #``
    (``YYYY-NNNNNNNN``). The old parser read column 0 as the name, so it stored
    the booking number as the name, the name as the booking number and the DOB
    as the booking date.
  * Detail ``/Details/-<id>``: card header ``Booking Details - YYYY-NNNNNNNN``
    (must equal the listing Booking #), ``Bonds`` card (Bond Amount column) and
    ``Charges`` card (Charge column) for that booking. Bond is the sum of the
    source Bond Amount cells; never synthesized. Details are fetched only for
    rows the source marks ``Released = No``.
  * Health stays unverified until a write smoke.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://inmatesearch.brevardsheriff.org"
FACILITY = "Brevard County Jail Complex"
LOOKBACK_DAYS = 7
MAX_DETAILS = 400
REQUEST_PAUSE_S = 0.25

BOOKING_RE = re.compile(r"^\d{4}-\d{8}$")
_MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
_DATETIME_RE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)$", re.I)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_URL + "/",
}

_REQUIRED_COLUMNS = ("booking #", "name", "booking date")


class BrevardContractError(RuntimeError):
    """Search form or results table no longer matches the verified contract."""


def parse_results_html(html: str) -> List[Dict[str, str]]:
    """Map results rows by header text. Raises on layout drift."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.find(string=re.compile(r"No results found", re.I)):
        return []
    table = soup.find("table")
    if table is None:
        raise BrevardContractError("Brevard: results page has no table")
    headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
    missing = [c for c in _REQUIRED_COLUMNS if c not in headers]
    if missing:
        raise BrevardContractError(f"Brevard: results header drift (missing {missing})")
    idx = {h: i for i, h in enumerate(headers)}
    rows: List[Dict[str, str]] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < len(headers):
            continue
        cell = lambda col: tds[idx[col]].get_text(" ", strip=True) if col in idx else ""  # noqa: E731
        link = tr.find("a", href=re.compile(r"/Details/", re.I))
        rows.append(
            {
                "booking": cell("booking #"),
                "name": cell("name"),
                "dob": cell("dob"),
                "booking_date": cell("booking date"),
                "released": cell("released"),
                "detail_url": urljoin(BASE_URL + "/", link["href"]) if link else "",
            }
        )
    return rows


def parse_detail_html(html: str, expected_booking: str) -> Optional[Dict[str, str]]:
    """Bonds + charges for the booking in the detail header; None if it mismatches."""
    soup = BeautifulSoup(html, "html.parser")
    header = soup.find("div", class_="card-header")
    m = re.search(r"Booking Details\s*-\s*(\d{4}-\d{8})", header.get_text(" ", strip=True) if header else "")
    if not m or m.group(1) != expected_booking:
        return None

    def card_table(title: str):
        for h in soup.find_all("div", class_="card-header"):
            if h.get_text(" ", strip=True).lower() == title:
                card = h.find_parent("div", class_="card")
                return card.find("table") if card else None
        return None

    def column(table, name: str) -> List[str]:
        if table is None:
            return []
        heads = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
        if name not in heads:
            return []
        i = heads.index(name)
        out = []
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) > i:
                out.append(tds[i].get_text(" ", strip=True))
        return out

    total = 0.0
    seen_amount = False
    for raw in column(card_table("bonds"), "bond amount"):
        money = _MONEY_RE.search(raw)
        if money:
            total += float(money.group(1).replace(",", ""))
            seen_amount = True
    bond_types = [t for t in column(card_table("bonds"), "bond type") if t]
    charges = [c for c in column(card_table("charges"), "charge") if c]
    sex = race = ""
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        label = dt.get_text(" ", strip=True).lower()
        if dd is None:
            continue
        if label == "gender":
            sex = dd.get_text(" ", strip=True)
        elif label == "race":
            race = dd.get_text(" ", strip=True)
    return {
        "bond": (f"{total:.2f}" if seen_amount else ""),
        "bond_type": " | ".join(bond_types),
        "charges": " | ".join(charges),
        "sex": sex,
        "race": race,
    }


def _split_name(name: str):
    name = " ".join((name or "").split())
    if "," in name:
        last, rest = [p.strip() for p in name.split(",", 1)]
        parts = rest.split()
        return (parts[0] if parts else ""), " ".join(parts[1:]), last
    parts = name.split()
    if len(parts) < 2:
        return (parts[0] if parts else ""), "", ""
    return parts[0], " ".join(parts[1:-1]), parts[-1]


class BrevardCountyScraper(BaseScraper):
    """Brevard County (FL) — BCSO Inmate Search date-range results + detail."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Brevard"

    @property
    def state(self) -> str:
        return "FL"

    def scrape(self, lookback_days: Optional[int] = None) -> List[ArrestRecord]:
        days = lookback_days or LOOKBACK_DAYS
        session = requests.Session()
        session.headers.update(HEADERS)

        home = session.get(BASE_URL + "/", timeout=30)
        home.raise_for_status()
        soup = BeautifulSoup(home.text, "html.parser")
        token = soup.find("input", {"name": "__RequestVerificationToken"})
        to_input = soup.find("input", {"name": "SearchForm.ToDate"})
        if token is None or to_input is None or not token.get("value"):
            raise BrevardContractError("Brevard: search form drift (token/ToDate missing)")
        try:
            to_date = datetime.strptime(to_input.get("max") or "", "%Y-%m-%d")
        except ValueError:
            to_date = datetime.now() - timedelta(days=1)
        from_date = to_date - timedelta(days=days)

        data = [
            ("SearchForm.FromDate", from_date.strftime("%Y-%m-%d")),
            ("__Invariant", "SearchForm.FromDate"),
            ("SearchForm.ToDate", to_date.strftime("%Y-%m-%d")),
            ("__Invariant", "SearchForm.ToDate"),
            ("SearchForm.LastName", ""),
            ("SearchForm.FirstName", ""),
            ("SearchForm.SubjectNumber", ""),
            ("SearchForm.BookingNumber", ""),
            ("__RequestVerificationToken", token["value"]),
        ]
        resp = session.post(BASE_URL + "/?handler=Search", data=data, timeout=90)
        resp.raise_for_status()
        rows = parse_results_html(resp.text)

        records: List[ArrestRecord] = []
        seen = set()
        details = 0
        for row in rows:
            booking = row["booking"]
            if not BOOKING_RE.fullmatch(booking) or booking in seen or not row["name"]:
                continue
            seen.add(booking)
            released = row["released"].strip().lower() == "yes"
            detail: Dict[str, str] = {}
            if not released and row["detail_url"] and details < MAX_DETAILS:
                details += 1
                try:
                    d = session.get(row["detail_url"], timeout=25)
                    if d.status_code == 200:
                        detail = parse_detail_html(d.text, booking) or {}
                except requests.RequestException as exc:
                    logger.debug("Brevard detail failed (%s): %s", row["detail_url"], exc)
                time.sleep(REQUEST_PAUSE_S)

            booking_date, booking_time = row["booking_date"], ""
            m = _DATETIME_RE.match(booking_date)
            if m:
                booking_date, booking_time = m.group(1), m.group(2)
            first, middle, last = _split_name(row["name"])
            sex_raw = (detail.get("sex") or "").lower()
            records.append(
                ArrestRecord(
                    County=self.county,
                    State="FL",
                    Facility=FACILITY,
                    Full_Name=row["name"],
                    First_Name=first,
                    Middle_Name=middle,
                    Last_Name=last,
                    DOB=row["dob"],
                    Sex="M" if sex_raw.startswith("m") else ("F" if sex_raw.startswith("f") else ""),
                    Race=detail.get("race", ""),
                    Booking_Number=booking,
                    Booking_Date=booking_date,
                    Booking_Time=booking_time,
                    Charges=detail.get("charges", ""),
                    Bond_Amount=detail.get("bond") or "0",
                    Bond_Type=detail.get("bond_type", ""),
                    Status="Released" if released else "In Custody",
                    Detail_URL=row["detail_url"],
                    LastCheckedMode="INITIAL",
                )
            )

        if rows and not records:
            raise BrevardContractError("Brevard: results rows present but none carry a source Booking #")
        logger.info(
            "Brevard: %d source bookings (%s..%s), %d detail pages",
            len(records), from_date.date(), to_date.date(), details,
        )
        return records
