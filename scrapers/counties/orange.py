"""
Orange County (FL) Arrest Scraper — OCFL BestJail public inmate JSON.

Source contract (recon 2026-10-08, docs/recon/FL_ORANGE_BESTJAIL_2026-10-08.md):
  * Portal: https://netapps.ocfl.net/BestJail/Home/Inmates — the county's public
    inmate search. Its own page script calls three JSON endpoints, used here
    with plain ``requests`` and TLS verification on (curl_cffi impersonation
    retired):
      - ``getInmates/<letter>``: current inmates whose name starts with the
        letter → ``[{bookingNumber, inmateName}]``. All 26 letters are walked;
        any non-200 / non-list reply raises (no silent partial roster).
      - ``getInmateDetails/<bookingNumber>``: one-element list with
        ``BOOKING`` (must equal the requested booking), ``DATEBOOKED``
        (MM/DD/YYYY), ``TIMEBOOKED`` (H:MMam/pm), ``BIRTH`` (age in years, not
        a date of birth), ``GENDER``, ``RACE``, ``CELL`` and address fields.
      - ``getCharges/<bookingNumber>``: one row per charge with ``Charge``,
        ``BondAmount`` (``"1500.00"`` or blank), ``ArrestingAgency``,
        ``CourtCaseNumber``, ``CaseStatus``.
  * Booking_Number = source ``bookingNumber`` (8 digits, ``YY`` + sequence).
    The roster lists a booking once per name on file (aliases): almost every
    one of the 2,863 current bookings on 2026-10-08 came back under more than
    one name. Rows are deduped on the booking number and the person's name is
    the detail ``NAME`` (the booking's primary name), never an alias row.
  * Every letter had rows on 2026-10-08 (the rarest, ``x``, had 2). A reply
    with more than ``MAX_EMPTY_LETTERS`` empty letters is treated as a bad
    read and raises, so a transient empty reply never writes a partial run.
  * Booking numbers are sequential, so the newest bookings are walked first and
    the walk stops once ``STOP_AFTER_OLDER`` bookings in a row fall outside the
    ``LOOKBACK_DAYS`` window (or at ``MAX_DETAILS``). The old module fetched
    details for every current-year inmate (~2,400) and timed out.
  * Bond is the sum of the published ``BondAmount`` cells. No published cell
    (or no charges, or a failed charges fetch) → ``Bond_Amount=""`` (unknown),
    never ``$0.00``. A published ``0.00`` stays ``0.00``.
  * ``BIRTH`` is an age, so it goes to ``Age_At_Arrest``; DOB stays empty.
  * Everyone on the roster is in custody (``Status="In Custody"``).
  * Health stays ``unverified`` until a Leads Ops write smoke.
"""
from __future__ import annotations

import logging
import re
import string
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

from core.models import ArrestRecord
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://netapps.ocfl.net/BestJail/Home"
ROSTER_URL = f"{BASE_URL}/Inmates"
INMATES_URL = f"{BASE_URL}/getInmates"
DETAILS_URL = f"{BASE_URL}/getInmateDetails"
CHARGES_URL = f"{BASE_URL}/getCharges"
FACILITY = "Orange County Jail"

LOOKBACK_DAYS = 7
STOP_AFTER_OLDER = 15
MAX_DETAILS = 800
MAX_EMPTY_LETTERS = 3
REQUEST_TIMEOUT = 30
REQUEST_PAUSE_S = 0.25  # between per-booking detail/charges pairs
ROSTER_PAUSE_S = 0.5  # between the 26 getInmates letter calls

BOOKING_RE = re.compile(r"^\d{8}$")
_AMOUNT_RE = re.compile(r"^\$?\s*([\d,]+(?:\.\d{1,2})?)$")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": ROSTER_URL,
}


class OrangeContractError(RuntimeError):
    """The BestJail JSON no longer matches the verified contract."""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def parse_roster(pages: Dict[str, Any]) -> List[Tuple[str, str]]:
    """``{letter: getInmates JSON}`` → ``[(booking, first listed name)]``, newest first.

    A booking appears once per name on file (aliases), so the listed name is
    only a fallback; ``build_record`` uses the detail ``NAME``.
    """
    by_booking: Dict[str, str] = {}
    empty = [letter for letter, data in pages.items() if isinstance(data, list) and not data]
    if len(empty) > MAX_EMPTY_LETTERS:
        raise OrangeContractError(f"Orange: {len(empty)} letters returned no inmates (bad read)")
    for letter, data in pages.items():
        if not isinstance(data, list):
            raise OrangeContractError(f"Orange: getInmates/{letter} is not a JSON list")
        for row in data:
            if not isinstance(row, dict) or "bookingNumber" not in row or "inmateName" not in row:
                raise OrangeContractError(f"Orange: getInmates/{letter} row drift")
            booking = _clean(row["bookingNumber"])
            name = _clean(row["inmateName"])
            if not BOOKING_RE.fullmatch(booking):
                raise OrangeContractError(f"Orange: malformed bookingNumber {booking!r}")
            if not name:
                raise OrangeContractError(f"Orange: booking {booking} has no name")
            by_booking.setdefault(booking, name)
    return sorted(by_booking.items(), key=lambda kv: kv[0], reverse=True)


def parse_booked_at(detail: Dict[str, Any]) -> Optional[datetime]:
    date_s = _clean(detail.get("DATEBOOKED"))
    time_s = _clean(detail.get("TIMEBOOKED")).replace(" ", "").upper()
    if not date_s:
        return None
    try:
        if time_s:
            return datetime.strptime(f"{date_s} {time_s}", "%m/%d/%Y %I:%M%p")
        return datetime.strptime(date_s, "%m/%d/%Y")
    except ValueError:
        return None


def parse_detail(data: Any, booking: str) -> Optional[Dict[str, Any]]:
    """The detail row for ``booking``; None when missing or naming another booking."""
    row = data[0] if isinstance(data, list) and data else None
    if not isinstance(row, dict) or _clean(row.get("BOOKING")) != booking:
        return None
    return row


CHARGE_ROW_KEYS = ("Charge", "BondAmount")


def parse_charges(data: Any) -> Dict[str, Any]:
    """Charges + bond from a successful getCharges response.

    The total bond is the sum of ``BondAmount`` only when every charge row
    publishes an amount (a published 0.00 counts). If any cell is blank or
    unparsed, the total is '' (unknown), and per-charge amounts stay in
    ``details``. ``None`` means the per-booking
    fetch itself failed (bond and charges unknown for that booking). Any other
    shape, or a row without ``Charge``/``BondAmount``, is contract drift and
    raises, so a changed response can never blank known charges or bonds.
    """
    if data is None:
        return {"charges": [], "details": [], "bond": "", "agency": "", "case_numbers": []}
    if not isinstance(data, list):
        raise OrangeContractError("Orange: getCharges is not a JSON list")
    charges: List[str] = []
    details: List[Dict[str, Any]] = []
    amounts: List[float] = []
    any_unpublished = False
    agency = ""
    cases: List[str] = []
    for row in data:
        if not isinstance(row, dict) or any(k not in row for k in CHARGE_ROW_KEYS):
            raise OrangeContractError("Orange: getCharges row drift")
        charge = _clean(row.get("Charge"))
        raw_bond = _clean(row.get("BondAmount"))
        m = _AMOUNT_RE.match(raw_bond)
        amount = float(m.group(1).replace(",", "")) if m else None
        if amount is not None:
            amounts.append(amount)
        else:
            any_unpublished = True  # blank/unparsed cell: may be a hold
        case = _clean(row.get("CourtCaseNumber"))
        if case and case not in cases:
            cases.append(case)
        if not agency:
            agency = _clean(row.get("ArrestingAgency"))
        if charge:
            charges.append(charge)
            details.append(
                {
                    "charge": charge,
                    "description": charge,
                    # None = unknown (blank cell); a published 0.00 stays 0.0
                    "bond_amount": amount,
                    "case_number": case,
                    "case_status": _clean(row.get("CaseStatus")),
                }
            )
    return {
        "charges": charges,
        "details": details,
        # Total only when every charge publishes an amount: a blank cell can be
        # a hold, so a partial sum would understate the bond. Unknown = "".
        "bond": f"{sum(amounts):.2f}" if amounts and not any_unpublished else "",
        "agency": agency,
        "case_numbers": cases,
    }


def _split_name(name: str) -> Tuple[str, str, str]:
    if "," in name:
        last, rest = [p.strip() for p in name.split(",", 1)]
        parts = rest.split()
        return (parts[0] if parts else ""), " ".join(parts[1:]), last
    parts = name.split()
    if len(parts) < 2:
        return (parts[0] if parts else ""), "", ""
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def build_record(booking: str, name: str, detail: Dict[str, Any], charges: Dict[str, Any]) -> Optional[ArrestRecord]:
    booked_at = parse_booked_at(detail)
    if booked_at is None:
        return None  # a booking date is part of the contract; never invent one
    full_name = _clean(detail.get("NAME"))
    if not full_name:
        return None  # primary name comes from the detail; alias rows are not used
    first, middle, last = _split_name(full_name)
    sex = _clean(detail.get("GENDER")).upper()[:1]
    age = _clean(detail.get("BIRTH"))
    street = _clean(detail.get("STREET"))
    apt = _clean(detail.get("APTNUM"))
    city = _clean(detail.get("CITY"))
    state = _clean(detail.get("STATE"))
    zip_code = _clean(detail.get("ZIPCODE"))
    address = ", ".join(p for p in [street, f"Apt {apt}" if apt else "", city, state, zip_code] if p)
    return ArrestRecord(
        County="Orange",
        State="FL",
        Facility=FACILITY,
        Booking_Number=booking,
        Full_Name=full_name,
        First_Name=first,
        Middle_Name=middle,
        Last_Name=last,
        Age_At_Arrest=age if age.isdigit() else "",
        Sex=sex if sex in ("M", "F") else "",
        Race=_clean(detail.get("RACE")),
        Address=address,
        City=city,
        ZIP=zip_code,
        Booking_Date=booked_at.strftime("%m/%d/%Y"),
        Booking_Time=booked_at.strftime("%I:%M %p"),
        Charges=" | ".join(charges["charges"]),
        Bond_Amount=charges["bond"],
        Case_Number=" | ".join(charges["case_numbers"]),
        Agency=charges["agency"],
        Status="In Custody",
        Detail_URL=ROSTER_URL,
        LastCheckedMode="INITIAL",
        extra_data={
            "charge_details": charges["details"],
            "booking_date_origin": "BestJail DATEBOOKED/TIMEBOOKED",
        },
    )


class OrangeCountyScraper(BaseScraper):
    """Orange County (FL) — BestJail public JSON, newest bookings first."""

    SOURCE_CONTRACT_VALIDATED = True

    @property
    def county(self) -> str:
        return "Orange"

    @property
    def state(self) -> str:
        return "FL"

    @property
    def roster_url(self) -> str:
        return ROSTER_URL

    def _get_json(self, session: requests.Session, url: str) -> Any:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def scrape(self, lookback_days: Optional[int] = None) -> List[ArrestRecord]:
        days = lookback_days or LOOKBACK_DAYS
        session = requests.Session()
        session.headers.update(HEADERS)

        pages: Dict[str, Any] = {}
        for i, letter in enumerate(string.ascii_lowercase):
            if i:
                time.sleep(ROSTER_PAUSE_S)
            try:
                pages[letter] = self._get_json(session, f"{INMATES_URL}/{letter}")
            except (requests.RequestException, ValueError) as exc:
                raise OrangeContractError(f"Orange: getInmates/{letter} failed: {exc}") from exc
        roster = parse_roster(pages)
        if not roster:
            raise OrangeContractError("Orange: empty roster for all 26 letters")

        cutoff = datetime.now() - timedelta(days=days)
        records: List[ArrestRecord] = []
        older_in_a_row = 0
        details = detail_failures = charge_failures = 0
        for booking, name in roster:
            if details >= MAX_DETAILS or older_in_a_row >= STOP_AFTER_OLDER:
                break
            details += 1
            time.sleep(REQUEST_PAUSE_S)
            try:
                detail = parse_detail(self._get_json(session, f"{DETAILS_URL}/{booking}"), booking)
            except (requests.RequestException, ValueError) as exc:
                logger.debug("Orange detail failed (%s): %s", booking, exc)
                detail = None
            if detail is None:
                detail_failures += 1
                continue
            booked_at = parse_booked_at(detail)
            if booked_at is None:
                detail_failures += 1
                continue
            if booked_at < cutoff:
                older_in_a_row += 1
                continue
            older_in_a_row = 0
            time.sleep(REQUEST_PAUSE_S)
            try:
                charge_data = self._get_json(session, f"{CHARGES_URL}/{booking}")
            except (requests.RequestException, ValueError) as exc:
                logger.debug("Orange charges failed (%s): %s", booking, exc)
                charge_data = None
                charge_failures += 1
            rec = build_record(booking, name, detail, parse_charges(charge_data))
            if rec is not None:
                records.append(rec)

        if details and detail_failures == details:
            raise OrangeContractError("Orange: every detail fetch failed or mismatched its booking")
        in_window = len(records)
        if in_window and charge_failures >= in_window:
            raise OrangeContractError("Orange: every getCharges fetch failed")
        if detail_failures or charge_failures:
            logger.warning(
                "Orange: %d detail and %d charge fetches failed (those bookings skipped / bond unknown)",
                detail_failures, charge_failures,
            )
        logger.info(
            "Orange: %d roster bookings, %d detail pages, %d within %d days",
            len(roster), details, len(records), days,
        )
        return records
