"""
Indian River County (FL) Arrest Scraper — IRCSO booking search + booking details.

Source contract (recon 2026-10-08, docs/recon/FL_INDIAN_RIVER_BOOKING_SEARCH_2026-10-08.md):
  * Portal: https://www.ircsheriff.org/inmate-search (ordinary public HTTPS,
    plain ``requests`` with TLS verification on; the old curl_cffi
    impersonation, ``verify=False`` and DrissionPage fallback are retired).
  * Listing: the page's own search form (``POST /booking-search/search`` with
    the form's ``_token`` and ``booking_date=MM/DD/YYYY``) returns the bookings
    for that date, 10 per page; later pages are
    ``GET /booking-search/search?booking_date=…&page=N``. Each result links to
    ``/booking-details/<id>``. The ``<id>`` is a portal id, **not** the booking
    number. The old module stored that id as ``Booking_Number`` and wrote ``0``
    when no bond was shown.
  * Detail: the ``Booking Info`` table has ``Booking Number``
    (``YYYY-NNNNNNNN``, the source booking key), ``Booking Date`` /
    ``Arrest Date`` (``Month Dth, YYYY at H:MM am``), ``Arresting Agency``,
    ``Case Number``, ``Bond`` (``$1,234.00``, ``No Bond`` or absent) and,
    once released, ``Release Date``. Charges are ``Charges`` cards (header =
    charge description).
  * Booking_Number = detail ``Booking Number``; a detail without one (or with a
    malformed one) or without a parseable Booking Date is dropped. The booking
    date must be the searched date, or the row is dropped.
  * Bond: ``$amount`` → amount; ``No Bond`` → ``Bond_Type="NO BOND"`` with the
    amount left empty; absent → empty (unknown). Never ``0`` unless published.
  * Health stays ``unverified`` until a Leads Ops write smoke.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from html import unescape
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ircsheriff.org"
SEARCH_PAGE_URL = f"{BASE_URL}/inmate-search"
SEARCH_URL = f"{BASE_URL}/booking-search/search"
DETAIL_URL = f"{BASE_URL}/booking-details"
FACILITY = "Indian River County Jail"
LOOKBACK_DAYS = 7
MAX_PAGES_PER_DAY = 30
MAX_DETAILS = 400
REQUEST_TIMEOUT = 30
REQUEST_PAUSE_S = 0.2

BOOKING_RE = re.compile(r"^\d{4}-\d{8}$")
_DETAIL_ID_RE = re.compile(r"/booking-details/(\d+)")
_MONEY_RE = re.compile(r"^\$\s*([\d,]+(?:\.\d{1,2})?)$")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": SEARCH_PAGE_URL,
}


class IndianRiverContractError(RuntimeError):
    """The booking search or detail page no longer matches the verified contract."""


def _clean(text: str) -> str:
    return " ".join(unescape(text or "").split())


def parse_token(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="searchform")
    token = form.find("input", {"name": "_token"}) if form else None
    if token is None or not token.get("value"):
        raise IndianRiverContractError("Indian River: booking search form / _token missing")
    for field in ("booking_date", "lname", "fname"):
        if form.find("input", {"name": field}) is None:
            raise IndianRiverContractError(f"Indian River: search form field {field!r} missing")
    return token["value"]


def parse_result_ids(html: str) -> List[str]:
    """Portal detail ids on one results page, in page order."""
    return list(dict.fromkeys(_DETAIL_ID_RE.findall(html)))


def parse_source_datetime(text: str) -> Optional[datetime]:
    """``October 6th, 2026 at 9:05 pm`` → datetime."""
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", _clean(text))
    for fmt in ("%B %d, %Y at %I:%M %p", "%B %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def parse_detail(html: str) -> Dict[str, object]:
    """Labelled fields + charge cards from one booking-details page."""
    soup = BeautifulSoup(html, "html.parser")
    fields: Dict[str, str] = {}
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != 2 or tds[0].find("strong") is None:
            continue
        label = _clean(tds[0].get_text(" ", strip=True)).rstrip(":")
        if label and label not in fields:
            fields[label] = _clean(tds[1].get_text(" ", strip=True))
    charges: List[str] = []
    heading = soup.find(lambda t: t.name in ("h3", "h4") and _clean(t.get_text()) == "Charges")
    if heading is not None:
        for header in heading.find_all_next("div", class_="card-header"):
            text = _clean(header.get_text(" ", strip=True))
            if text:
                charges.append(text)
    return {"fields": fields, "charges": charges}


def parse_bond(value: str) -> Dict[str, str]:
    value = _clean(value)
    if not value:
        return {"amount": "", "type": ""}
    if value.lower() == "no bond":
        return {"amount": "", "type": "NO BOND"}
    m = _MONEY_RE.match(value)
    if m:
        return {"amount": f"{float(m.group(1).replace(',', '')):.2f}", "type": ""}
    return {"amount": "", "type": ""}


def _split_name(name: str):
    name = _clean(name)
    if "," in name:
        last, rest = [p.strip() for p in name.split(",", 1)]
        parts = rest.split()
        return (parts[0] if parts else ""), " ".join(parts[1:]), last
    parts = name.split()
    if len(parts) < 2:
        return (parts[0] if parts else ""), "", ""
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def build_record(detail: Dict[str, object], detail_url: str) -> Optional[ArrestRecord]:
    fields: Dict[str, str] = detail["fields"]  # type: ignore[assignment]
    booking = fields.get("Booking Number", "")
    booked_at = parse_source_datetime(fields.get("Booking Date", ""))
    name = fields.get("Name", "")
    if not BOOKING_RE.fullmatch(booking) or booked_at is None or not name:
        return None
    arrested_at = parse_source_datetime(fields.get("Arrest Date", ""))
    released_at = parse_source_datetime(fields.get("Release Date", ""))
    first, middle, last = _split_name(name)
    bond = parse_bond(fields.get("Bond", ""))
    dob = re.sub(r"\s*\(.*\)$", "", fields.get("Date of Birth", ""))
    dob_dt = parse_source_datetime(dob)
    sex = fields.get("Sex", "").upper()[:1]
    charges: List[str] = detail["charges"]  # type: ignore[assignment]
    return ArrestRecord(
        County="Indian River",
        State="FL",
        Facility=FACILITY,
        Booking_Number=booking,
        Full_Name=name,
        First_Name=first,
        Middle_Name=middle,
        Last_Name=last,
        DOB=dob_dt.strftime("%m/%d/%Y") if dob_dt else "",
        Sex=sex if sex in ("M", "F") else "",
        Race=fields.get("Race", ""),
        Booking_Date=booked_at.strftime("%m/%d/%Y"),
        Booking_Time=booked_at.strftime("%I:%M %p"),
        Arrest_Date=arrested_at.strftime("%m/%d/%Y") if arrested_at else "",
        Arrest_Time=arrested_at.strftime("%I:%M %p") if arrested_at else "",
        Agency=fields.get("Arresting Agency", ""),
        Case_Number=fields.get("Case Number", ""),
        Charges=" | ".join(charges),
        Bond_Amount=bond["amount"],  # "" = unknown / not published
        Bond_Type=bond["type"],
        Status="Released" if released_at else "In Custody",
        Release_Date=released_at.strftime("%m/%d/%Y") if released_at else "",
        Detail_URL=detail_url,
        LastCheckedMode="INITIAL",
        extra_data={
            "charge_details": [{"charge": c, "description": c, "bond_amount": None} for c in charges],
            "booking_date_origin": "IRCSO booking-details Booking Date",
        },
    )


class IndianRiverCountyScraper(BaseScraper):
    """Indian River County (FL) — booking-date search → booking details."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Indian River"

    @property
    def state(self) -> str:
        return "FL"

    def _session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(HEADERS)
        return session

    def _ids_for_day(self, session: requests.Session, token: str, day: datetime) -> List[str]:
        date_s = day.strftime("%m/%d/%Y")
        resp = session.post(
            SEARCH_URL,
            data={"_token": token, "lname": "", "fname": "", "booking_date": date_s,
                  "release_date": "", "booking_number": "", "dob": ""},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        ids = parse_result_ids(resp.text)
        for page in range(2, MAX_PAGES_PER_DAY + 1):
            if not ids or len(ids) < 10 * (page - 1):
                break
            more = session.get(SEARCH_URL, params={"booking_date": date_s, "page": page}, timeout=REQUEST_TIMEOUT)
            more.raise_for_status()
            new = [i for i in parse_result_ids(more.text) if i not in ids]
            if not new:
                break
            ids.extend(new)
            time.sleep(REQUEST_PAUSE_S)
        else:
            raise IndianRiverContractError(f"Indian River: {date_s} still paging at MAX_PAGES_PER_DAY")
        return ids

    def scrape(self, lookback_days: Optional[int] = None) -> List[ArrestRecord]:
        days = lookback_days or LOOKBACK_DAYS
        session = self._session()
        landing = session.get(SEARCH_PAGE_URL, timeout=REQUEST_TIMEOUT)
        landing.raise_for_status()
        token = parse_token(landing.text)

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        wanted: List[tuple] = []
        for offset in range(days):
            day = today - timedelta(days=offset)
            for detail_id in self._ids_for_day(session, token, day):
                wanted.append((detail_id, day.date()))
            time.sleep(REQUEST_PAUSE_S)

        records: List[ArrestRecord] = []
        seen: set = set()
        fetched = dropped = 0
        for detail_id, day in wanted[:MAX_DETAILS]:
            url = f"{DETAIL_URL}/{detail_id}"
            fetched += 1
            try:
                resp = session.get(url, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                rec = build_record(parse_detail(resp.text), url)
            except requests.RequestException as exc:
                logger.debug("Indian River detail failed (%s): %s", url, exc)
                rec = None
            if rec is None or rec.Booking_Number in seen or datetime.strptime(rec.Booking_Date, "%m/%d/%Y").date() != day:
                dropped += 1
            else:
                seen.add(rec.Booking_Number)
                records.append(rec)
            time.sleep(REQUEST_PAUSE_S)

        if fetched and not records:
            raise IndianRiverContractError("Indian River: details fetched but none carry a source Booking Number")
        if dropped:
            logger.warning("Indian River: dropped %d/%d details (no key/date, wrong date or failed)", dropped, fetched)
        logger.info("Indian River: %d bookings over %d days (%d details)", len(records), days, fetched)
        return records

    def _fetch_single_booking(self, booking_id: str, detail_url: str) -> Optional[ArrestRecord]:
        """Custody recheck: re-read one stored booking. None unless the page still
        shows the same source Booking Number (legacy rows keyed on the portal id
        never match and are reported as not found)."""
        if not detail_url or not _DETAIL_ID_RE.search(detail_url) or not detail_url.startswith(BASE_URL + "/"):
            return None
        try:
            resp = self._session().get(detail_url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Indian River re-fetch failed for %s: %s", detail_url, exc)
            return None
        rec = build_record(parse_detail(resp.text), detail_url)
        if rec is None or rec.Booking_Number != booking_id:
            return None
        rec.LastCheckedMode = "RECHECK"
        return rec
