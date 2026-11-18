"""Sarasota FL contract checks. No network.

The current-inmate listing is still only a person link id, a name, and a
date of birth. ``assess_listing`` records that. The 2026-11-18 detail recon
found a real Booking Number on the desktop charge table, so ``assess_detail``
and ``parse_detail_page`` are the reopen gate. ``SarasotaCountyScraper`` stays
fail-closed and does not call them.

A booking key is the 12-digit Booking Number cell only. The person PIN, the
court case number, and the dates are never turned into a booking number.
"""
from __future__ import annotations

import html as _html
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from bs4 import BeautifulSoup

from core.models import ArrestRecord

logger = logging.getLogger(__name__)

LISTING_URL = "https://cms.revize.com/revize/apps/sarasota/"

_ENTRY_RE = re.compile(
    r'href="viewInmate\.php\?id=(\d+)\s*"[^>]*>\s*([^<]+?)\s*</a>', re.I
)
# Per-entry proof: each entry itself must carry a labelled booking number with
# a value and a booking date *with a time*. Page-wide labels (an empty table
# header, help text) prove nothing about the entries.
_ENTRY_BOOKING_RE = re.compile(
    r"booking\s*(?:#|no\.?|number)\s*[:#]?\s*(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{3,19}\b",
    re.I,
)
_ENTRY_BOOKING_TIME_RE = re.compile(
    r"book(?:ing|ed)?\s*(?:date|time|date/time)?\s*[:#]?\s*"
    r"\d{1,2}/\d{1,2}/\d{2,4}[ T,]+\d{1,2}:\d{2}",
    re.I,
)


@dataclass(frozen=True)
class SarasotaListingVerdict:
    reopen_ok: bool
    listed: int
    unique_link_ids: int
    reasons: Tuple[str, ...] = field(default_factory=tuple)


def listing_entries(page_html: str) -> List[Tuple[str, str]]:
    """``[(link_id, label)]`` from the current-inmate dropdown."""
    return [(m.group(1), _html.unescape(m.group(2)).strip()) for m in _ENTRY_RE.finditer(page_html or "")]


def assess_listing(page_html: str) -> SarasotaListingVerdict:
    """Decide whether the listing alone satisfies the booking-safe contract.

    Every entry must itself expose a source booking number and a booking
    date/time; labels elsewhere on the page do not count.
    """
    entries = listing_entries(page_html)
    reasons: List[str] = []
    if not entries:
        reasons.append("no current-inmate listing entries")
    labels = [label for _, label in entries]
    if not labels or not all(_ENTRY_BOOKING_RE.search(lb) for lb in labels):
        reasons.append("no source booking number on the listing (link id only)")
    if not labels or not all(_ENTRY_BOOKING_TIME_RE.search(lb) for lb in labels):
        reasons.append("no booking date/time on the listing")
    ids = [e[0] for e in entries]
    if len(set(ids)) != len(ids):
        reasons.append("duplicate link ids on the listing")
    return SarasotaListingVerdict(
        reopen_ok=not reasons,
        listed=len(entries),
        unique_link_ids=len(set(ids)),
        reasons=tuple(reasons),
    )


# About 1,065 Current Inmate Population entries, each one detail page.
# One run fetches a Hernando-sized cap and leaves the rest for a later run.
MAX_DETAILS_PER_RUN = 200
REQUEST_PAUSE_S = 0.4
STATUS_ON_ROSTER = "current_inmate_population_and_blank_release_date_time"
STATUS_RELEASE_FILLED = "release_date_time_filled"
STATUS_RELEASE_CONFLICT = "release_date_time_conflict"

_BOOKING_RE = re.compile(r"^\d{12}$")
_INTAKE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$")
_MONEY_RE = re.compile(r"^\$(\d+)\.(\d{2})$")
_LABEL_DATE_RE = re.compile(r"\s*-\s*\d{1,2}/\d{1,2}/\d{4}\s*$")
_PERSON_LABELS = {
    "pin": "pin",
    "race": "race",
    "sex": "sex",
    "location": "location",
}


@dataclass(frozen=True)
class SarasotaDetailVerdict:
    reopen_ok: bool
    bookings: int
    skipped_rows: int
    reasons: Tuple[str, ...] = field(default_factory=tuple)


def split_listing_label(label: str) -> Dict[str, str]:
    """Name parts from ``LAST,FIRST MIDDLE - MM/DD/YYYY``. The date is dropped."""
    text = " ".join(_html.unescape(label or "").split())
    text = _LABEL_DATE_RE.sub("", text).strip()
    last, sep, rest = text.partition(",")
    parts = rest.split() if sep else []
    return {
        "full_name": text,
        "last": last.strip() if sep else "",
        "first": parts[0] if parts else "",
        "middle": " ".join(parts[1:]),
    }


def _bond_cell(cell: str) -> Tuple[Optional[float], str]:
    """Published dollars, or ``(None, text)`` when the cell is not a dollar amount.

    ``$0.00`` is a published 0. ``No Bond Available`` stays text. Blank is unknown.
    """
    text = " ".join((cell or "").split())
    if not text:
        return None, ""
    if text.casefold() == "no bond available":
        return None, text
    match = _MONEY_RE.fullmatch(text)
    if not match:
        return None, text
    value = int(match.group(1)) + int(match.group(2)) / 100
    return value, text


def _format_total(total: float) -> str:
    if float(total).is_integer():
        return str(int(total))
    return f"{total:.2f}"


def _personal_fields(soup: BeautifulSoup) -> Dict[str, str]:
    found: Dict[str, str] = {}
    for tr in soup.find_all("tr"):
        if tr.find_parent("table", id="data-table") is not None:
            continue
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        label = cells[0].get_text(" ", strip=True).rstrip(":").strip().lower()
        if label == "date of birth":
            continue
        key = _PERSON_LABELS.get(label)
        if key and key not in found:
            found[key] = cells[1].get_text(" ", strip=True)
    return found


def _header_index(table) -> Optional[Dict[str, int]]:
    head = table.find("thead")
    header_tr = head.find("tr") if head is not None else table.find("tr")
    if header_tr is None:
        return None
    headers = [cell.get_text(" ", strip=True).lower() for cell in header_tr.find_all(["th", "td"])]
    index = {name: pos for pos, name in enumerate(headers)}
    if "booking number" not in index or "intake date/time" not in index:
        return None
    return index


def _data_rows(table) -> List:
    body = table.find("tbody")
    if body is not None:
        return body.find_all("tr", recursive=False)
    rows = table.find_all("tr")
    return rows[1:] if rows else []


def _cell(cells: Sequence[str], index: Dict[str, int], name: str) -> str:
    pos = index.get(name)
    if pos is None or pos >= len(cells):
        return ""
    return cells[pos].strip()


def parse_detail_page(
    page_html: str,
    *,
    pin: str = "",
    label: str = "",
    on_roster: bool = False,
) -> Tuple[List[ArrestRecord], Dict[str, int]]:
    """One record per distinct 12-digit Booking Number on ``table#data-table``.

    Rows without that booking number, or without an Intake Date/Time, are
    skipped. A stacked table that is not ``#data-table`` is ignored. The PIN
    is ``Person_ID`` only.
    """
    soup = BeautifulSoup(page_html or "", "html.parser")
    stats = {
        "table_missing": 0,
        "columns_missing": 0,
        "skipped_rows": 0,
        "intake_conflicts": 0,
    }
    table = soup.find("table", id="data-table")
    if table is None:
        stats["table_missing"] = 1
        return [], stats
    index = _header_index(table)
    if index is None:
        stats["columns_missing"] = 1
        return [], stats

    person = _personal_fields(soup)
    names = split_listing_label(label)
    pin_value = (pin or person.get("pin") or "").strip()
    grouped: Dict[str, dict] = {}
    order: List[str] = []

    for tr in _data_rows(table):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        booking = _cell(cells, index, "booking number")
        intake = _cell(cells, index, "intake date/time")
        if not _BOOKING_RE.fullmatch(booking) or booking == pin_value:
            stats["skipped_rows"] += 1
            continue
        if not _INTAKE_RE.fullmatch(intake):
            stats["skipped_rows"] += 1
            continue
        bond_amount, bond_text = _bond_cell(_cell(cells, index, "bond amount"))
        charge = {
            "charge": _cell(cells, index, "offense description"),
            "description": _cell(cells, index, "offense description"),
            "counts": _cell(cells, index, "counts"),
            "arraign_date": _cell(cells, index, "arraign date"),
            "bond_raw": bond_text,
            "bond_amount": bond_amount,
            "bond_text": bond_text if bond_amount is None else "",
            "bond_type": _cell(cells, index, "bond type"),
            "intake": intake,
            "case_number": _cell(cells, index, "court case number"),
            "release": _cell(cells, index, "release date/time"),
            "hold": _cell(cells, index, "hold"),
        }
        group = grouped.get(booking)
        if group is None:
            grouped[booking] = group = {"intake": intake, "conflict": False, "charges": []}
            order.append(booking)
        elif group["intake"] != intake:
            group["conflict"] = True
            stats["intake_conflicts"] += 1
        group["charges"].append(charge)

    records: List[ArrestRecord] = []
    for booking in order:
        group = grouped[booking]
        if group["conflict"]:
            continue
        charges = group["charges"]
        releases = {c["release"] for c in charges if c["release"]}
        if not releases and on_roster:
            status = "In Custody"
            status_source = STATUS_ON_ROSTER
            release_date = ""
        elif len(releases) == 1:
            status = ""
            status_source = STATUS_RELEASE_FILLED
            release_date = next(iter(releases))
        elif len(releases) > 1:
            status = ""
            status_source = STATUS_RELEASE_CONFLICT
            release_date = ""
        else:
            status = ""
            status_source = ""
            release_date = ""
        amounts = [c["bond_amount"] for c in charges]
        bond_amount = _format_total(sum(amounts)) if all(amount is not None for amount in amounts) else ""
        cases: List[str] = []
        for charge in charges:
            if charge["case_number"] and charge["case_number"] not in cases:
                cases.append(charge["case_number"])
        arraign_dates = [c["arraign_date"] for c in charges if c["arraign_date"]]
        court_date = arraign_dates[0] if len(set(arraign_dates)) == 1 else ""
        intake_date, _, intake_time = group["intake"].partition(" ")
        extra = {
            "booking_key_origin": "detail Booking Number",
            "person_pin": pin_value,
            "location": person.get("location", ""),
            "bond_published": bond_amount != "",
            "charge_details": charges,
        }
        if status_source:
            extra["status_source"] = status_source
        records.append(ArrestRecord(
            County="Sarasota",
            State="FL",
            Booking_Number=booking,
            Person_ID=pin_value,
            Full_Name=names["full_name"],
            First_Name=names["first"],
            Middle_Name=names["middle"],
            Last_Name=names["last"],
            Race=person.get("race", ""),
            Sex=person.get("sex", ""),
            Booking_Date=intake_date,
            Booking_Time=intake_time,
            Charges=" | ".join(c["description"] for c in charges if c["description"]),
            Bond_Amount=bond_amount,
            Bond_Type="",
            Case_Number=" | ".join(cases),
            Court_Date=court_date,
            Status=status,
            Release_Date=release_date,
            Facility="Sarasota County Jail",
            Detail_URL=f"{LISTING_URL}viewInmate.php?id={pin_value}" if pin_value else "",
            extra_data=extra,
        ))
    return records, stats


def assess_detail(page_html: str, *, pin: str = "", on_roster: bool = True) -> SarasotaDetailVerdict:
    """True only when the desktop table yields at least one source booking key."""
    records, stats = parse_detail_page(page_html, pin=pin, on_roster=on_roster)
    reasons: List[str] = []
    if stats["table_missing"]:
        reasons.append("no desktop charge table")
    elif stats["columns_missing"]:
        reasons.append("charge table is missing Booking Number or Intake Date/Time")
    elif not records:
        reasons.append("no charge row with a 12-digit Booking Number and an Intake Date/Time")
    for record in records:
        if not _BOOKING_RE.fullmatch(record.Booking_Number):
            reasons.append("booking key was not a 12-digit Booking Number")
        if record.Person_ID and record.Booking_Number == record.Person_ID:
            reasons.append("person PIN used as the booking key")
        if not record.Booking_Date or not record.Booking_Time:
            reasons.append("accepted booking has no Intake Date/Time")
    return SarasotaDetailVerdict(
        reopen_ok=bool(records) and not reasons,
        bookings=len(records),
        skipped_rows=stats["skipped_rows"],
        reasons=tuple(reasons),
    )


def walk_detail_pages(
    listing_html: str,
    fetch_detail: Callable[[str], str],
    *,
    sleep: Callable[[float], None],
    limit: int = MAX_DETAILS_PER_RUN,
    pause_s: float = REQUEST_PAUSE_S,
) -> Tuple[List[ArrestRecord], Dict[str, int]]:
    """Fetch at most ``limit`` detail pages, pausing before each one.

    ``fetch_detail`` receives the person PIN. The listing label's date is not
    stored. Pins past the cap are not requested.
    """
    seen: Dict[str, str] = {}
    order: List[str] = []
    for pin, label in listing_entries(listing_html):
        if pin in seen:
            continue
        seen[pin] = label
        order.append(pin)
    chosen = order[:limit]
    records: List[ArrestRecord] = []
    failures = 0
    for pin in chosen:
        sleep(pause_s)
        try:
            page_html = fetch_detail(pin)
        except Exception as exc:  # noqa: BLE001 - one failed page does not invent a row
            failures += 1
            logger.warning("Sarasota detail fetch failed (%s)", type(exc).__name__)
            continue
        page_records, _stats = parse_detail_page(
            page_html, pin=pin, label=seen[pin], on_roster=True,
        )
        records.extend(page_records)
    summary = {
        "listed": len(order),
        "fetched": len(chosen),
        "skipped_over_cap": len(order) - len(chosen),
        "fetch_failures": failures,
        "records": len(records),
    }
    logger.info(
        "Sarasota detail walk: listed %d, fetched %d, skipped_over_cap %d, failures %d, records %d",
        summary["listed"], summary["fetched"], summary["skipped_over_cap"],
        summary["fetch_failures"], summary["records"],
    )
    return records, summary
