"""
Okaloosa County (FL) Arrest Scraper — Inmate Locator public JSON API (ProPhoenix).

Source contract (recon 2026-10-08, docs/recon/FL_OKALOOSA_API_2026-10-08.md):
  * The official Inmate Locator (https://okaloosacountyjail.myokaloosa.com/InmateLocator/,
    linked from https://www.sheriff-okaloosa.org/) is an Angular app. Its
    ``config.json`` names the public API base
    ``https://okaloosacountyjail.myokaloosa.com/InmateLocatorAPI``, and the app
    itself calls:
      - ``GET /api/Inmates/search?page=N&pageSize=M``: the current roster,
        ``{total, page, pageSize, data: [{bookingNo, fullName, custodyDate,
        totalBondAmt, status, sex, race, dobDttm, spnNo, nameID, ...}]}``;
      - ``GET /api/Inmates/<bookingNo>``: one booking with ``charges`` rows
        ``{charge (statute), chargeDesc, severity, bailAmt, bailType, caseNbr,
        courtDate}``.
    Plain ``requests``, TLS verification on; no login, CAPTCHA or WAF.
  * The old module scraped the legacy Default.aspx Infragistics grid by walking
    flattened cells. That grid has no booking date, so rows were saved with no
    booking date (2026-10-07 note). ``custodyDate`` is the booking timestamp.
  * Booking_Number = source ``bookingNo`` (10 digits, ``YYYY`` + sequence).
    Paging must reach ``total`` with unique booking numbers, or it raises.
  * Only ``status == "1"`` rows are emitted as In Custody (781 of 783 on
    2026-10-08). Any other status code is undocumented, so those rows are skipped
    and counted, not guessed.
  * Bond: the roster ``totalBondAmt`` equals the sum of the detail ``bailAmt``
    values when any are published (24/24 checked); when none are, both are
    0/null. The bond is the sum of the detail ``bailAmt`` only when every
    charge publishes one (a published 0 counts). Any blank charge, which can
    be a hold, makes the total ``""``. Without a detail (outside the window)
    the bond is ``""``: the roster total cannot show a blank charge, so it is
    not used.
  * Charges come from the detail, fetched only for bookings within
    ``LOOKBACK_DAYS`` (newest first, at most ``MAX_DETAILS``). A failed or
    mismatched detail leaves charges empty; it is never invented.
  * Health stays ``unverified`` until a Leads Ops write smoke.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

logger = logging.getLogger(__name__)

PORTAL_URL = "https://okaloosacountyjail.myokaloosa.com/InmateLocator/"
API_BASE = "https://okaloosacountyjail.myokaloosa.com/InmateLocatorAPI"
SEARCH_URL = f"{API_BASE}/api/Inmates/search"
DETAIL_URL = f"{API_BASE}/api/Inmates"
FACILITY = "Okaloosa County Jail"
PAGE_SIZE = 100
MAX_PAGES = 40
LOOKBACK_DAYS = 7
MAX_DETAILS = 300
REQUEST_TIMEOUT = 30
REQUEST_PAUSE_S = 0.1
IN_CUSTODY_STATUS = "1"

BOOKING_RE = re.compile(r"^\d{10}$")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": PORTAL_URL,
}


class OkaloosaContractError(RuntimeError):
    """The Inmate Locator API no longer matches the verified contract."""


def _clean(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split())


def _money(value: Any) -> Optional[float]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except ValueError:
        return None


def parse_custody_date(value: Any) -> Optional[datetime]:
    text = _clean(value)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_search_page(data: Any) -> Tuple[int, List[Dict[str, Any]]]:
    if not isinstance(data, dict) or not isinstance(data.get("data"), list) or not isinstance(data.get("total"), int):
        raise OkaloosaContractError("Okaloosa: search reply shape drift")
    for row in data["data"]:
        if not isinstance(row, dict) or "bookingNo" not in row or "custodyDate" not in row:
            raise OkaloosaContractError("Okaloosa: search row drift")
    return data["total"], data["data"]


def parse_detail(data: Any, booking: str) -> Optional[Dict[str, Any]]:
    """Charges + bond for ``booking``; None when missing or naming another booking."""
    if not isinstance(data, dict) or _clean(data.get("bookingNo")) != booking:
        return None
    charges: List[str] = []
    details: List[Dict[str, Any]] = []
    amounts: List[float] = []
    any_unpublished = False
    cases: List[str] = []
    for row in data.get("charges") or []:
        if not isinstance(row, dict):
            continue
        desc = _clean(row.get("chargeDesc"))
        statute = _clean(row.get("charge"))
        amount = _money(row.get("bailAmt"))
        if amount is not None:
            amounts.append(amount)
        else:
            any_unpublished = True  # blank bailAmt: may be a hold
        case = _clean(row.get("caseNbr"))
        if case and case not in cases:
            cases.append(case)
        text = desc or statute
        if not text:
            continue
        charges.append(text)
        details.append(
            {
                "charge": text,
                "description": desc,
                "statute": statute,
                "degree": _clean(row.get("severity")),
                "bond_amount": amount,  # None = not published
                "bond_type": _clean(row.get("bailType")),
                "case_number": case,
                "court_date": _clean(row.get("courtDate")),
            }
        )
    return {
        "charges": charges,
        "details": details,
        # Total only when every charge publishes bailAmt; a blank can be a hold,
        # so a partial sum would understate the bond. "" = unknown.
        "bond": f"{sum(amounts):.2f}" if amounts and not any_unpublished else "",
        "case_numbers": cases,
        "release_date": _clean(data.get("releaseDate")),
    }


def _split_name(name: str) -> Tuple[str, str, str]:
    name = _clean(name)
    if "," in name:
        last, rest = [p.strip() for p in name.split(",", 1)]
        parts = rest.split()
        return (parts[0] if parts else ""), " ".join(parts[1:]), last
    parts = name.split()
    if len(parts) < 2:
        return (parts[0] if parts else ""), "", ""
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def build_record(row: Dict[str, Any], detail: Optional[Dict[str, Any]]) -> Optional[ArrestRecord]:
    booking = _clean(row.get("bookingNo"))
    booked_at = parse_custody_date(row.get("custodyDate"))
    full_name = _clean(row.get("fullName"))
    if not BOOKING_RE.fullmatch(booking) or booked_at is None or not full_name:
        return None
    first, middle, last = _split_name(full_name)
    # Only the detail can show whether every charge published a bailAmt. The
    # roster totalBondAmt is the sum of the published ones (on 2026-10-08, 9 of
    # 60 sampled bookings mixed a blank charge with a positive total), so it
    # understates a bond with a blank (maybe a hold) and is never used.
    bond = detail["bond"] if detail is not None else ""
    sex = _clean(row.get("sex")).upper()[:1]
    dob = parse_custody_date(row.get("dobDttm"))
    extra: Dict[str, Any] = {"booking_date_origin": "Inmate Locator API custodyDate"}
    if detail is not None:
        extra["charge_details"] = detail["details"]
        if detail["release_date"]:
            extra["source_release_date"] = detail["release_date"]
    return ArrestRecord(
        County="Okaloosa",
        State="FL",
        Facility=FACILITY,
        Booking_Number=booking,
        Person_ID=_clean(row.get("spnNo")),
        Full_Name=full_name,
        First_Name=first,
        Middle_Name=middle,
        Last_Name=last,
        DOB=dob.strftime("%m/%d/%Y") if dob else "",
        Sex=sex if sex in ("M", "F") else "",
        Race=_clean(row.get("race")),
        Booking_Date=booked_at.strftime("%m/%d/%Y"),
        Booking_Time=booked_at.strftime("%I:%M %p"),
        Charges=" | ".join(detail["charges"]) if detail else "",
        Bond_Amount=bond,
        Case_Number=" | ".join(detail["case_numbers"]) if detail else "",
        Status="In Custody",
        Detail_URL=PORTAL_URL,
        LastCheckedMode="INITIAL",
        extra_data=extra,
    )


class OkaloosaCountyScraper(BaseScraper):
    """Okaloosa County (FL) — Inmate Locator public JSON (Crestview)."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Okaloosa"

    @property
    def state(self) -> str:
        return "FL"

    def _get_json(self, session: requests.Session, url: str, params: Optional[dict] = None) -> Any:
        resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def fetch_roster(self, session: requests.Session) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        total = None
        for page in range(1, MAX_PAGES + 1):
            try:
                data = self._get_json(session, SEARCH_URL, {"page": page, "pageSize": PAGE_SIZE})
            except (requests.RequestException, ValueError) as exc:
                raise OkaloosaContractError(f"Okaloosa: search page {page} failed: {exc}") from exc
            page_total, page_rows = parse_search_page(data)
            if total is None:
                total = page_total
            elif page_total != total:
                raise OkaloosaContractError("Okaloosa: roster total changed while paging")
            rows.extend(page_rows)
            if len(rows) >= total or not page_rows:
                break
        else:
            raise OkaloosaContractError(f"Okaloosa: MAX_PAGES={MAX_PAGES} reached before total")
        bookings = [_clean(r.get("bookingNo")) for r in rows]
        if not total or len(rows) != total:
            raise OkaloosaContractError(f"Okaloosa: walked {len(rows)} rows, source total {total}")
        if len(set(bookings)) != len(bookings):
            raise OkaloosaContractError("Okaloosa: duplicate bookingNo across pages")
        bad = [b for b in bookings if not BOOKING_RE.fullmatch(b)]
        if bad:
            raise OkaloosaContractError(f"Okaloosa: {len(bad)} malformed bookingNo values")
        return rows

    def scrape(self, lookback_days: Optional[int] = None) -> List[ArrestRecord]:
        days = lookback_days or LOOKBACK_DAYS
        session = requests.Session()
        session.headers.update(HEADERS)
        rows = self.fetch_roster(session)

        cutoff = datetime.now() - timedelta(days=days)
        current = [r for r in rows if _clean(r.get("status")) == IN_CUSTODY_STATUS]
        skipped_status = len(rows) - len(current)
        current.sort(key=lambda r: _clean(r.get("bookingNo")), reverse=True)

        records: List[ArrestRecord] = []
        details = detail_failures = 0
        for row in current:
            booking = _clean(row.get("bookingNo"))
            booked_at = parse_custody_date(row.get("custodyDate"))
            detail = None
            if booked_at is not None and booked_at >= cutoff and details < MAX_DETAILS:
                details += 1
                try:
                    detail = parse_detail(self._get_json(session, f"{DETAIL_URL}/{booking}"), booking)
                except (requests.RequestException, ValueError) as exc:
                    logger.debug("Okaloosa detail failed (%s): %s", booking, exc)
                if detail is None:
                    detail_failures += 1
                time.sleep(REQUEST_PAUSE_S)
            rec = build_record(row, detail)
            if rec is not None:
                records.append(rec)

        if current and not records:
            raise OkaloosaContractError("Okaloosa: roster rows present but none carry booking key + date")
        if skipped_status or detail_failures:
            logger.warning(
                "Okaloosa: skipped %d rows with non-custody status; %d/%d detail fetches failed",
                skipped_status, detail_failures, details,
            )
        logger.info(
            "Okaloosa: %d roster rows, %d emitted, %d details (%d-day window)",
            len(rows), len(records), details, days,
        )
        return records
